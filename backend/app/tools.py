"""
FUNCTION CALLING: as funções que o Gemini pode chamar.

Cada função tem duas partes:
1. A DECLARAÇÃO (nome, descrição e parâmetros em JSON Schema), enviada ao Gemini em `tools`.
   É por ela que a IA sabe que a função existe e quando usá-la.
2. A IMPLEMENTAÇÃO em Python, executada pelo backend quando o Gemini pede.

O Gemini nunca executa nada: ele só responde "chame consultar_banco com sql=...".
Quem executa, valida e devolve o resultado é o backend (classe ToolRunner).
"""
import time
from typing import Callable, Optional

from google.genai import types as gtypes

from . import database, external
from .database import QueryExecutionError, UnsafeQueryError
from .external import TYPES, ExternalAPIError, NotFoundError

MAX_ROWS_TO_MODEL = 25  # o Gemini recebe no máximo 25 linhas (economiza tokens); o app recebe todas

# ---------------------------------------------------------------------------
# 1) DECLARAÇÕES enviadas ao Gemini
# ---------------------------------------------------------------------------
FUNCTION_DECLARATIONS = [
    gtypes.FunctionDeclaration(
        name="consultar_banco",
        description=(
            "Executa UMA consulta SQL (SQLite, somente SELECT) no banco local de Pokémon. "
            "Use para rankings, filtros e comparações de tipo, status base, região, geração, "
            "habilidades e formas. Inclua sempre p.id quando o resultado for uma lista de Pokémon."),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Consulta SELECT usando a tabela pokemon com alias p."},
                "explicacao": {"type": "string", "description": "Frase curta, em português, explicando o critério."},
            },
            "required": ["sql", "explicacao"],
        },
    ),
    gtypes.FunctionDeclaration(
        name="descricao_pokedex",
        description=(
            "Busca AO VIVO na PokéAPI a descrição da Pokédex de um Pokémon, a categoria "
            "(ex.: 'Flame Pokémon'), habitat, cor, grupos de ovo e felicidade base."),
        parameters_json_schema={
            "type": "object",
            "properties": {"pokemon": {"type": "string", "description": "Nome do Pokémon em inglês, ex.: 'charizard'."}},
            "required": ["pokemon"],
        },
    ),
    gtypes.FunctionDeclaration(
        name="linha_evolutiva",
        description=(
            "Busca AO VIVO na PokéAPI a linha evolutiva completa de um Pokémon, com a condição "
            "de cada evolução (nível, item, troca, amizade...)."),
        parameters_json_schema={
            "type": "object",
            "properties": {"pokemon": {"type": "string", "description": "Nome do Pokémon em inglês."}},
            "required": ["pokemon"],
        },
    ),
    gtypes.FunctionDeclaration(
        name="fraquezas_e_resistencias",
        description=(
            "Calcula com a tabela de tipos da PokéAPI (AO VIVO) as fraquezas, resistências e "
            "imunidades de um Pokémon ou de uma combinação de tipos. Informe 'pokemon' OU 'tipos'."),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "pokemon": {"type": "string", "description": "Nome do Pokémon (os tipos vêm do banco)."},
                "tipos": {"type": "array", "items": {"type": "string", "enum": TYPES},
                          "minItems": 1, "maxItems": 2, "description": "Um ou dois tipos em inglês."},
            },
        },
    ),
    gtypes.FunctionDeclaration(
        name="cartas_tcg",
        description="Busca AO VIVO na API TCGdex as cartas mais recentes do Pokémon Estampas Ilustradas (TCG).",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "pokemon": {"type": "string", "description": "Nome do Pokémon em inglês."},
                "quantidade": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Padrão: 6."},
            },
            "required": ["pokemon"],
        },
    ),
]

TOOL = gtypes.Tool(function_declarations=FUNCTION_DECLARATIONS)

SOURCES = {
    "consultar_banco": "Banco local",
    "descricao_pokedex": "PokéAPI",
    "linha_evolutiva": "PokéAPI",
    "fraquezas_e_resistencias": "PokéAPI",
    "cartas_tcg": "TCGdex",
}
MAX_CALLS_PER_FUNCTION = 4


# ---------------------------------------------------------------------------
# 2) IMPLEMENTAÇÃO: executa as chamadas pedidas pelo Gemini e guarda os resultados para o app
# ---------------------------------------------------------------------------
class ToolRunner:
    """Executa as funções pedidas pelo Gemini e junta tudo o que o app vai mostrar."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.calls: list = []              # rastro das chamadas (mostrado no app)
        self.pokemon_ids: list = []        # ids para montar os cards, na ordem em que apareceram
        self.query_values: dict = {}       # colunas extras da consulta por id
        self.table: Optional[dict] = None  # resultado sem id (contagens, médias)
        self.sql: Optional[str] = None
        self.explanation: str = ""
        self.truncated = False
        self.row_count = 0
        self.description: Optional[dict] = None
        self.evolution: Optional[list] = None
        self.weaknesses: Optional[dict] = None
        self.cards: Optional[dict] = None
        self._counts: dict = {}

    # chamado pelo laço de function calling para cada function_call do Gemini
    def execute(self, name: str, args: dict) -> dict:
        started = time.perf_counter()
        handler: Optional[Callable[[dict], dict]] = getattr(self, f"_fn_{name}", None)
        self._counts[name] = self._counts.get(name, 0) + 1
        if handler is None:
            result = {"erro": f"A função '{name}' não existe."}
        elif self._counts[name] > MAX_CALLS_PER_FUNCTION:
            result = {"erro": f"Limite de {MAX_CALLS_PER_FUNCTION} chamadas de {name} por pergunta atingido."}
        else:
            try:
                result = handler(args or {})
            except NotFoundError:
                result = {"erro": "Não encontrado na API externa. Confira o nome do Pokémon."}
            except ExternalAPIError as exc:
                result = {"erro": f"API externa indisponível: {exc}"}
        ok = "erro" not in result
        self.calls.append({
            "name": name,
            "source": SOURCES.get(name, "?"),
            "args": args or {},
            "ok": ok,
            "summary": result.get("erro") if not ok else self._summary(name, result),
            "ms": int((time.perf_counter() - started) * 1000),
        })
        return result

    @staticmethod
    def _summary(name: str, result: dict) -> str:
        if name == "consultar_banco":
            return f"{result.get('total_linhas', 0)} linha(s)"
        if name == "linha_evolutiva":
            return f"{len(result.get('estagios', []))} Pokémon na linha evolutiva"
        if name == "fraquezas_e_resistencias":
            return f"{len(result.get('fraquezas', {}))} fraqueza(s)"
        if name == "descricao_pokedex":
            return f"descrição encontrada ({result.get('categoria') or 'sem categoria'})"
        if name == "cartas_tcg":
            return f"{result.get('total_encontradas', 0)} carta(s) encontradas"
        return "ok"

    def _add_ids(self, ids: list) -> None:
        for i in ids:
            if i not in self.pokemon_ids:
                self.pokemon_ids.append(i)

    def _resolve(self, args: dict) -> Optional[dict]:
        return database.resolve_pokemon(str(args.get("pokemon") or ""))

    # ----- funções -----
    def _fn_consultar_banco(self, args: dict) -> dict:
        sql = str(args.get("sql") or "")
        try:
            result = database.run_safe_query(sql)
        except UnsafeQueryError as exc:
            return {"erro": f"Consulta bloqueada por segurança: {exc} Use apenas um SELECT."}
        except QueryExecutionError as exc:
            return {"erro": f"Erro no SQL: {exc}. Corrija a consulta e chame a função de novo."}
        rows = result["rows"]
        self.sql = result["sql"]
        self.explanation = str(args.get("explicacao") or "")
        self.truncated = result["truncated"]
        self.row_count = len(rows)
        if "id" in result["columns"]:
            ids = [r["id"] for r in rows if isinstance(r.get("id"), int)]
            self._add_ids(ids)
            for r in rows:
                if isinstance(r.get("id"), int):
                    self.query_values[r["id"]] = {k: v for k, v in r.items() if k != "id"}
        else:
            self.table = {"columns": result["columns"], "rows": rows}
        return {"total_linhas": len(rows), "lista_truncada": result["truncated"],
                "linhas": rows[:MAX_ROWS_TO_MODEL]}

    def _fn_descricao_pokedex(self, args: dict) -> dict:
        p = self._resolve(args)
        if not p:
            return {"erro": f"Pokémon '{args.get('pokemon')}' não encontrado no banco."}
        entry = external.pokedex_entry(p["species_identifier"])
        self._add_ids([p["id"]])
        self.description = {"pokemon": p["name"], "id": p["id"], "genus": entry["categoria"],
                            "text": entry["descricao_en"] or entry["descricao_es"],
                            "language": "en" if entry["descricao_en"] else "es",
                            "version": entry["versao_do_jogo"]}
        return {"pokemon": p["name"], **entry}

    def _fn_linha_evolutiva(self, args: dict) -> dict:
        p = self._resolve(args)
        if not p:
            return {"erro": f"Pokémon '{args.get('pokemon')}' não encontrado no banco."}
        stages = external.evolution_chain(p["species_identifier"])
        self._add_ids([p["id"]])
        ids = database.base_ids_by_dex_numbers([s["numero_pokedex"] for s in stages if s.get("numero_pokedex")])
        cards = database.fetch_pokemon_by_ids([ids[s["numero_pokedex"]] for s in stages if s.get("numero_pokedex") in ids])
        by_dex = {c["pokedex_number"]: c for c in cards}
        self.evolution = [{
            "species": s["especie"],
            "name": by_dex.get(s.get("numero_pokedex"), {}).get("name", s["especie"].title()),
            "id": by_dex.get(s.get("numero_pokedex"), {}).get("id"),
            "sprite_url": by_dex.get(s.get("numero_pokedex"), {}).get("sprite_url"),
            "types": by_dex.get(s.get("numero_pokedex"), {}).get("types", []),
            "from": s["evolui_de"], "condition": s["condicao"], "stage": s["estagio"],
        } for s in stages]
        return {"pokemon": p["name"], "estagios": stages, "fonte": "PokéAPI /evolution-chain"}

    def _fn_fraquezas_e_resistencias(self, args: dict) -> dict:
        label = None
        tipos = [str(t).lower() for t in (args.get("tipos") or [])]
        if args.get("pokemon"):
            p = self._resolve(args)
            if not p:
                return {"erro": f"Pokémon '{args.get('pokemon')}' não encontrado no banco."}
            tipos, label = p["types"], p["name"]
            self._add_ids([p["id"]])
        if not tipos or len(tipos) > 2 or any(t not in TYPES for t in tipos):
            return {"erro": "Informe um Pokémon ou de 1 a 2 tipos válidos (em inglês, ex.: 'fire')."}
        result = external.weaknesses(tipos)
        self.weaknesses = {"pokemon": label, "types": tipos, "weak": result["fraquezas"],
                           "resist": result["resistencias"], "immune": result["imunidades"]}
        return {"pokemon": label, **result}

    def _fn_cartas_tcg(self, args: dict) -> dict:
        p = self._resolve(args)
        name = p["species"] if p else str(args.get("pokemon") or "").strip()
        if not name:
            return {"erro": "Informe o nome do Pokémon."}
        try:
            limit = max(1, min(12, int(args.get("quantidade") or 6)))
        except (TypeError, ValueError):
            limit = 6
        result = external.tcg_cards(name, limit)
        if p:
            self._add_ids([p["id"]])
        self.cards = {"pokemon": name, "total": result["total_encontradas"], "cards": result["cartas"]}
        return {"pokemon": name, **result}

