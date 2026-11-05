"""
Testes do DataDex. Não usam internet nem gastam cota do Gemini:
- o Gemini é substituído por um "roteiro" de function calls;
- as APIs externas respondem com cópias reais da PokéAPI (tests/fixtures, tiradas do
  repositório oficial PokeAPI/api-data) e com uma amostra no formato da TCGdex.
Rodar:  cd backend && python -m pytest -q
"""
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app import external, main
from app.database import UnsafeQueryError, run_safe_query, validate_sql
from app.llm import GeminiClient, LLMError, QuotaExceededError
from app.services import DataDexService
from app.tools import FUNCTION_DECLARATIONS, ToolRunner

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
URL_FIXTURES = {
    "/pokemon-species/eevee": "species_eevee.json",
    "/pokemon-species/gengar": "species_gengar.json",
    "/evolution-chain/67": "chain_67.json",
    "/evolution-chain/40": "chain_40.json",
    "/type/ghost": "type_ghost.json",
    "/type/poison": "type_poison.json",
    "/type/fire": "type_fire.json",
    "/type/psychic": "type_psychic.json",
    "/cards": "tcg_pikachu_sample.json",
}


@pytest.fixture(autouse=True)
def fake_external(monkeypatch):
    """Substitui o GET das APIs externas por arquivos locais e registra as URLs pedidas."""
    requested = []

    def fake_get(url, params=None):
        requested.append((url, params))
        for suffix, fname in URL_FIXTURES.items():
            if url.rstrip("/").endswith(suffix):
                with open(os.path.join(FIX, fname), encoding="utf-8") as fh:
                    return json.load(fh)
        raise external.NotFoundError("404")

    monkeypatch.setattr(external.http, "get", fake_get)
    return requested


# ---------------------------------------------------------------------------
# Gemini falso: segue um roteiro de function calls e chama o ToolRunner de verdade
# ---------------------------------------------------------------------------
ALOLA_FIRE = ("SELECT p.id, p.name, p.total FROM pokemon p WHERE (p.type1 = 'fire' OR p.type2 = 'fire') "
              "AND p.region = 'alola' AND p.form_category NOT IN ('mega','gigantamax') ORDER BY p.total DESC LIMIT 1")

SCRIPTS = {
    "alola": [[("consultar_banco", {"sql": ALOLA_FIRE, "explicacao": "fogo de Alola por total"})]],
    "quantos": [[("consultar_banco", {"sql": "SELECT type1 AS tipo, COUNT(*) AS quantidade FROM pokemon GROUP BY type1",
                                      "explicacao": "contagem por tipo"})]],
    "evolui": [[("linha_evolutiva", {"pokemon": "eevee"})]],
    "gengar": [[("descricao_pokedex", {"pokemon": "gengar"}), ("fraquezas_e_resistencias", {"pokemon": "gengar"})]],
    "cartas": [[("cartas_tcg", {"pokemon": "pikachu", "quantidade": 5})]],
    # duas rodadas: primeiro descobre no banco, depois busca as fraquezas
    "mais forte de kanto": [
        [("consultar_banco", {"sql": "SELECT p.id, p.name FROM pokemon p WHERE p.region='kanto' "
                                     "AND p.form_category='base' ORDER BY p.total DESC LIMIT 1", "explicacao": "x"})],
        [("fraquezas_e_resistencias", {"pokemon": "Mewtwo"})],
    ],
    "corrige": [
        [("consultar_banco", {"sql": "SELECT p.id, p.nome_errado FROM pokemon p", "explicacao": "x"})],
        [("consultar_banco", {"sql": "SELECT p.id, p.name FROM pokemon p WHERE p.name = 'Pikachu'", "explicacao": "x"})],
    ],
    "apagar": [[("consultar_banco", {"sql": "DELETE FROM pokemon", "explicacao": "x"})]],
    "inexistente": [[("linha_evolutiva", {"pokemon": "naoexistemon"})]],
}


class ScriptedGemini:
    last_model = "fake-gemini"

    def __init__(self):
        self.calls = 0
        self.results = []

    def run_with_tools(self, system_instruction, contents, tool, runner, max_rounds=None):
        self.calls += 1
        question = contents[-1].parts[0].text.lower()
        if "bolo" in question:
            return "[FORA_DO_TEMA] Só respondo perguntas sobre Pokémon."
        if "oi tudo" in question:
            return "Olá! Tudo ótimo."  # respondeu sem chamar nenhuma função
        for key, rounds in SCRIPTS.items():
            if key in question:
                runner.reset()
                for calls in rounds:
                    self.results.append([runner.execute(n, a) for n, a in calls])
                return "Resposta baseada nos dados das funções."
        return "[FORA_DO_TEMA]"


@pytest.fixture()
def client(tmp_path):
    fake = ScriptedGemini()
    main.set_service(DataDexService(fake))
    main._hits.clear()
    with TestClient(main.app) as c:
        c.fake = fake
        yield c


def ask(client, q):
    return client.post("/api/ask", json={"question": q})


# ---------------------------------------------------------------------------
# Function calling ponta a ponta (API -> serviço -> funções -> banco / APIs externas)
# ---------------------------------------------------------------------------
def test_pergunta_do_enunciado_usa_consultar_banco(client):
    body = ask(client, "Qual o pokemon de fogo com os status base mais altos de Alola?").json()
    assert [p["name"] for p in body["pokemon"]] == ["Blacephalon"]
    assert body["tool_calls"][0]["name"] == "consultar_banco" and body["tool_calls"][0]["source"] == "Banco local"
    assert "region = 'alola'" in body["sql"]


def test_linha_evolutiva_vem_da_pokeapi(client, fake_external):
    body = ask(client, "Como o Eevee evolui?").json()
    evo = body["evolution"]
    assert evo[0]["name"] == "Eevee" and len(evo) == 9
    vaporeon = next(s for s in evo if s["species"] == "vaporeon")
    assert vaporeon["from"] == "eevee" and vaporeon["condition"] == "usar Water Stone"
    umbreon = next(s for s in evo if s["species"] == "umbreon")
    assert "amizade alta" in umbreon["condition"] and "de noite" in umbreon["condition"]
    assert vaporeon["sprite_url"] and vaporeon["id"] == 134
    assert any("/evolution-chain/67" in u for u, _ in fake_external)  # chamou a API externa
    assert body["tool_calls"][0]["source"] == "PokéAPI"


def test_duas_funcoes_na_mesma_rodada(client):
    body = ask(client, "O que é o Gengar e quais as fraquezas dele?").json()
    assert [c["name"] for c in body["tool_calls"]] == ["descricao_pokedex", "fraquezas_e_resistencias"]
    assert body["description"]["pokemon"] == "Gengar" and body["description"]["text"]
    w = body["weaknesses"]
    assert w["types"] == ["ghost", "poison"]
    # Fantasma/Venenoso: fraco a ghost, dark, ground, psychic; imune a normal e fighting
    assert set(w["weak"]) == {"ghost", "dark", "ground", "psychic"}
    assert set(w["immune"]) == {"normal", "fighting"}
    assert w["resist"]["bug"] == 0.25 and w["resist"]["poison"] == 0.25 and w["resist"]["grass"] == 0.5
    assert [p["name"] for p in body["pokemon"]] == ["Gengar"]


def test_cartas_tcg_vem_da_tcgdex(client, fake_external):
    body = ask(client, "Me mostre as cartas do Pikachu").json()
    cards = body["cards"]["cards"]
    assert body["cards"]["total"] == 3
    assert [c["nome"] for c in cards] == ["Pikachu ex", "Pikachu"]  # sem a carta sem imagem, mais recentes primeiro
    assert cards[0]["imagem"].endswith("/low.png")
    url, params = next(x for x in fake_external if x[0].endswith("/cards"))
    assert params == {"name": "Pikachu"} and "/en/cards" in url


def test_encadeamento_banco_depois_pokeapi(client, fake_external):
    body = ask(client, "Quais as fraquezas do Pokémon mais forte de Kanto?").json()
    assert [c["name"] for c in body["tool_calls"]] == ["consultar_banco", "fraquezas_e_resistencias"]
    assert body["weaknesses"]["pokemon"] == "Mewtwo"
    assert body["pokemon"][0]["name"] == "Mewtwo"


def test_agregacao_retorna_tabela(client):
    body = ask(client, "Quantos pokemon por tipo?").json()
    assert body["result_type"] == "table"
    assert body["table"]["columns"] == ["tipo", "quantidade"]


def test_ia_corrige_sql_com_o_erro_devolvido(client):
    body = ask(client, "consulta que corrige sozinha").json()
    first, second = body["tool_calls"]
    assert first["ok"] is False and "Erro no SQL" in first["summary"]
    assert second["ok"] is True and body["pokemon"][0]["name"] == "Pikachu"


def test_sql_perigoso_e_bloqueado_e_sem_dados_nao_responde(client):
    r = ask(client, "pode apagar tudo")
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_answerable"
    assert "bloqueada" in client.fake.results[-1][0]["erro"]


def test_pokemon_inexistente_vira_erro_para_a_ia(client):
    r = ask(client, "fale do inexistente naoexistemon")
    assert r.status_code == 422  # a função falhou e não há outra fonte: a API não inventa resposta
    assert "não encontrado" in client.fake.results[-1][0]["erro"]


@pytest.mark.parametrize("question", ["me passa uma receita de bolo", "oi tudo bem?"])
def test_sem_funcao_nao_ha_resposta(client, question):
    r = ask(client, question)
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_answerable"


@pytest.mark.parametrize("question", ["", "  ", "oi", "???", "x" * 400])
def test_validacao_da_pergunta(client, question):
    r = ask(client, question)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_question"


def test_cache_de_perguntas_repetidas(client):
    ask(client, "Como o Eevee evolui?")
    calls = client.fake.calls
    second = ask(client, "Como o Eevee evolui?").json()
    assert second["cached"] is True and client.fake.calls == calls


def test_cache_sobrevive_a_reinicio(tmp_path):
    path = str(tmp_path / "cache.json")
    DataDexService(ScriptedGemini(), cache_path=path).ask("Como o Eevee evolui?")
    novo = ScriptedGemini()
    body = DataDexService(novo, cache_path=path).ask("Como o Eevee evolui?")
    assert body["cached"] is True and novo.calls == 0


def test_rate_limit(client):
    object.__setattr__(main.settings, "rate_limit_per_minute", 2)
    try:
        codes = [ask(client, "Quantos pokemon?").status_code for _ in range(3)]
        assert codes[-1] == 429
    finally:
        object.__setattr__(main.settings, "rate_limit_per_minute", 20)


def test_health_lista_funcoes_e_apis(client):
    body = client.get("/api/health").json()
    assert body["functions"] == ["consultar_banco", "descricao_pokedex", "linha_evolutiva",
                                 "fraquezas_e_resistencias", "cartas_tcg"]
    assert "pokeapi" in body["external_apis"] and "tcgdex" in body["external_apis"]


def test_detalhe_e_404(client):
    assert client.get("/api/pokemon/25").json()["name"] == "Pikachu"
    assert client.get("/api/pokemon/999999").status_code == 404


# ---------------------------------------------------------------------------
# Laço de function calling do GeminiClient com respostas no formato real do SDK
# ---------------------------------------------------------------------------
class _ApiErr(Exception):
    def __init__(self, code):
        super().__init__(f"{code} erro simulado")
        self.code = code


def _call(name, args, cid):
    return types.FunctionCall(id=cid, name=name, args=args)


def _resp(calls=None, text=None):
    content = types.Content(role="model", parts=[types.Part(function_call=c) for c in calls or []]
                            or [types.Part.from_text(text=text or "")])
    return SimpleNamespace(function_calls=calls, text=text, candidates=[SimpleNamespace(content=content)])


class FakeModels:
    """Simula client.models.generate_content: por modelo, uma lista de respostas ou um código de erro."""

    def __init__(self, plan):
        self.plan = {m: (list(v) if isinstance(v, list) else v) for m, v in plan.items()}
        self.requests = []

    def generate_content(self, model, contents, config):
        self.requests.append((model, list(contents), config))
        step = self.plan[model]
        if isinstance(step, int):
            raise _ApiErr(step)
        return step.pop(0)


def _gemini(plan):
    models = FakeModels(plan)
    return GeminiClient(models=list(plan), client=SimpleNamespace(models=models)), models


def _user(q):
    return [types.Content(role="user", parts=[types.Part.from_text(text=q)])]


def test_laco_executa_funcao_e_devolve_function_response():
    g, models = _gemini({"m1": [
        _resp(calls=[_call("consultar_banco", {"sql": ALOLA_FIRE, "explicacao": "x"}, "call-1")]),
        _resp(text="O Blacephalon é o mais forte."),
    ]})
    runner = ToolRunner()
    text = g.run_with_tools("sistema", _user("pergunta"), types.Tool(function_declarations=FUNCTION_DECLARATIONS), runner)
    assert text == "O Blacephalon é o mais forte." and g.last_model == "m1"
    _, contents, config = models.requests[1]
    tool_msg = contents[-1]
    assert tool_msg.role == "user"
    fr = tool_msg.parts[0].function_response
    assert fr.id == "call-1" and fr.name == "consultar_banco" and fr.response["total_linhas"] == 1
    assert contents[-2].role == "model"  # a mensagem do modelo (com a function_call) foi mantida
    assert config.automatic_function_calling.disable is True
    assert [d.name for d in config.tools[0].function_declarations][0] == "consultar_banco"


def test_troca_de_modelo_quando_cota_acaba():
    g, _ = _gemini({"m1": 429, "m2": 404, "m3": [_resp(text="ok")]})
    assert g.run_with_tools("s", _user("q"), None, ToolRunner()) == "ok"
    assert g.last_model == "m3"


def test_todos_sem_cota_vira_quota_exceeded():
    g, _ = _gemini({"m1": 429, "m2": 404})
    with pytest.raises(QuotaExceededError):
        g.run_with_tools("s", _user("q"), None, ToolRunner())
    g, _ = _gemini({"m1": 500})
    with pytest.raises(LLMError) as err:
        g.run_with_tools("s", _user("q"), None, ToolRunner())
    assert not isinstance(err.value, QuotaExceededError)


def test_api_responde_429_quando_cota_acaba(client):
    class NoQuota(ScriptedGemini):
        def run_with_tools(self, *a, **k):
            raise QuotaExceededError("A cota gratuita diária do Gemini acabou.")

    main.set_service(DataDexService(NoQuota()))
    r = ask(client, "Qual o pokemon mais rápido?")
    assert r.status_code == 429 and r.json()["error"]["code"] == "gemini_quota_exceeded"


def test_limite_de_rodadas_forca_resposta_final():
    call = lambda i: _resp(calls=[_call("consultar_banco", {"sql": "SELECT 1 AS um", "explicacao": "x"}, f"c{i}")])  # noqa: E731
    g, models = _gemini({"m1": [call(i) for i in range(3)] + [_resp(text="final")]})
    assert g.run_with_tools("s", _user("q"), None, ToolRunner(), max_rounds=3) == "final"
    last_config = models.requests[-1][2]
    assert last_config.tool_config.function_calling_config.mode == "NONE"


# ---------------------------------------------------------------------------
# Segurança do SQL
# ---------------------------------------------------------------------------
def test_validate_sql_unidade():
    assert validate_sql("select 1;") == "select 1"
    for bad in ["update pokemon set hp=1", "PRAGMA table_info(pokemon)", "ATTACH 'x' AS y", "select 1; select 2"]:
        with pytest.raises(UnsafeQueryError):
            validate_sql(bad)


def test_limite_de_linhas():
    result = run_safe_query("SELECT id FROM pokemon")
    assert len(result["rows"]) == 50 and result["truncated"] is True


# ---------------------------------------------------------------------------
# Cliente HTTP das APIs externas (tempo limite, erros e cache)
# ---------------------------------------------------------------------------
def _http_client(handler):
    import httpx
    c = external.HttpJsonClient(timeout=1)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_http_cache_e_erros():
    import httpx
    hits = []

    def handler(request):
        hits.append(str(request.url))
        if request.url.path.endswith("/404"):
            return httpx.Response(404)
        if request.url.path.endswith("/500"):
            return httpx.Response(500)
        if request.url.path.endswith("/timeout"):
            raise httpx.ReadTimeout("lento", request=request)
        return httpx.Response(200, json={"ok": True})

    c = _http_client(handler)
    assert c.get("https://x.test/ok") == {"ok": True}
    assert c.get("https://x.test/ok") == {"ok": True} and len(hits) == 1  # segunda vez veio do cache
    with pytest.raises(external.NotFoundError):
        c.get("https://x.test/404")
    with pytest.raises(external.ExternalAPIError):
        c.get("https://x.test/500")
    with pytest.raises(external.ExternalAPIError) as err:
        c.get("https://x.test/timeout")
    assert "demorou" in str(err.value)


def test_api_externa_fora_do_ar_vira_erro_para_a_ia(monkeypatch):
    def down(url, params=None):
        raise external.ExternalAPIError("Não foi possível conectar à API externa.")

    monkeypatch.setattr(external.http, "get", down)
    result = ToolRunner().execute("linha_evolutiva", {"pokemon": "eevee"})
    assert "API externa indisponível" in result["erro"]
