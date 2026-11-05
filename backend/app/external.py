"""
Integração com APIs externas consultadas AO VIVO pelas funções do Gemini.

- PokéAPI  (https://pokeapi.co): descrição da Pokédex, linha evolutiva e tabela de tipos.
- TCGdex   (https://tcgdex.dev):  cartas do Pokémon Estampas Ilustradas (TCG).

Cada chamada tem tempo limite, tratamento de erro e cache em memória: a mesma URL
não é pedida de novo durante CACHE_TTL_S (os dados dessas APIs quase nunca mudam).
"""
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

import httpx

from .config import settings

log = logging.getLogger("datadex.external")

CACHE_TTL_S = 24 * 3600
CACHE_MAX = 500
TYPE_PT = {"normal": "Normal", "fire": "Fogo", "water": "Água", "grass": "Planta", "electric": "Elétrico",
           "ice": "Gelo", "fighting": "Lutador", "poison": "Venenoso", "ground": "Terrestre", "flying": "Voador",
           "psychic": "Psíquico", "bug": "Inseto", "rock": "Pedra", "ghost": "Fantasma", "dragon": "Dragão",
           "dark": "Sombrio", "steel": "Aço", "fairy": "Fada"}
TYPES = ["normal", "fire", "water", "grass", "electric", "ice", "fighting", "poison", "ground",
         "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]


class ExternalAPIError(RuntimeError):
    """Falha ao consultar uma API externa (fora do ar, tempo esgotado, resposta inválida)."""


class NotFoundError(ExternalAPIError):
    """A API externa respondeu 404: o recurso não existe."""


class HttpJsonClient:
    """GET de JSON com tempo limite e cache simples (LRU com validade)."""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or settings.external_timeout_s
        self._cache: "OrderedDict[str, tuple]" = OrderedDict()
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=True,
                                    headers={"User-Agent": "DataDex/2.0 (projeto academico)"})

    def get(self, url: str, params: Optional[dict] = None) -> Any:
        key = url + ("?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else "")
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            self._cache.move_to_end(key)
            return hit[1]
        try:
            resp = self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ExternalAPIError(f"A API demorou mais de {self.timeout:.0f} s para responder.") from exc
        except httpx.HTTPError as exc:
            raise ExternalAPIError(f"Não foi possível conectar à API externa ({type(exc).__name__}).") from exc
        if resp.status_code == 404:
            raise NotFoundError("Recurso não encontrado na API externa.")
        if resp.status_code >= 400:
            raise ExternalAPIError(f"A API externa respondeu com erro {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExternalAPIError("A API externa devolveu uma resposta inválida.") from exc
        self._cache[key] = (time.monotonic(), data)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)
        return data


http = HttpJsonClient()


def _abs(url: str) -> str:
    """A PokéAPI devolve URLs absolutas; aceita também caminhos relativos (/api/v2/...)."""
    if url.startswith("http"):
        return url
    base = settings.pokeapi_url.rstrip("/")
    return base + url[len("/api/v2"):] if url.startswith("/api/v2") else base + "/" + url.lstrip("/")


def _clean(text: str) -> str:
    return " ".join(text.replace("\x0c", " ").replace("\n", " ").split())


def _pick_language(entries: list, lang: str) -> Optional[dict]:
    """Última entrada no idioma pedido (a mais recente na PokéAPI)."""
    found = [e for e in entries if e.get("language", {}).get("name") == lang]
    return found[-1] if found else None


# ---------------- PokéAPI ----------------
def pokeapi_species(species_identifier: str) -> dict:
    """GET /pokemon-species/{nome}"""
    return http.get(f"{settings.pokeapi_url.rstrip('/')}/pokemon-species/{species_identifier}")


def pokedex_entry(species_identifier: str) -> dict:
    """Resumo da espécie: categoria (genus), descrição da Pokédex, habitat, grupos de ovo..."""
    s = pokeapi_species(species_identifier)
    flavor_en = _pick_language(s.get("flavor_text_entries", []), "en")
    flavor_es = _pick_language(s.get("flavor_text_entries", []), "es")
    genus = _pick_language(s.get("genera", []), "en")
    return {
        "especie": s.get("name"),
        "categoria": genus["genus"] if genus else None,
        "descricao_en": _clean(flavor_en["flavor_text"]) if flavor_en else None,
        "descricao_es": _clean(flavor_es["flavor_text"]) if flavor_es else None,
        "versao_do_jogo": (flavor_en or flavor_es or {}).get("version", {}).get("name"),
        "habitat": (s.get("habitat") or {}).get("name"),
        "cor": (s.get("color") or {}).get("name"),
        "grupos_de_ovo": [g["name"] for g in s.get("egg_groups", [])],
        "felicidade_base": s.get("base_happiness"),
        "taxa_de_captura": s.get("capture_rate"),
        "lendario": s.get("is_legendary"),
        "mitico": s.get("is_mythical"),
        "fonte": "PokéAPI /pokemon-species",
    }


def _humanize(slug: Optional[str]) -> Optional[str]:
    return slug.replace("-", " ").title() if slug else None


def _condition(details: list) -> str:
    """Traduz o primeiro conjunto de condições de evolução para português simples."""
    if not details:
        return ""
    d = details[0]
    trigger = (d.get("trigger") or {}).get("name")
    parts = []
    if d.get("min_level"):
        parts.append(f"nível {d['min_level']}")
    if d.get("item"):
        parts.append(f"usar {_humanize(d['item']['name'])}")
    if d.get("held_item"):
        parts.append(f"segurando {_humanize(d['held_item']['name'])}")
    if d.get("min_happiness"):
        parts.append("amizade alta")
    if d.get("min_affection"):
        parts.append("afeto alto")
    if d.get("known_move"):
        parts.append(f"sabendo {_humanize(d['known_move']['name'])}")
    if d.get("known_move_type"):
        t = d["known_move_type"]["name"]
        parts.append(f"sabendo golpe do tipo {TYPE_PT.get(t, t)}")
    if d.get("location"):
        parts.append(f"em {_humanize(d['location']['name'])}")
    if d.get("time_of_day") == "day":
        parts.append("de dia")
    elif d.get("time_of_day") == "night":
        parts.append("de noite")
    if trigger == "trade":
        parts.insert(0, "troca")
    if not parts:
        parts.append({"level-up": "subir de nível", "use-item": "usar item", "shed": "evolução especial"}
                     .get(trigger, _humanize(trigger) or "condição especial"))
    return ", ".join(parts)


def evolution_chain(species_identifier: str) -> list:
    """Linha evolutiva completa: lista de estágios com de quem evolui e a condição."""
    species = pokeapi_species(species_identifier)
    chain_url = (species.get("evolution_chain") or {}).get("url")
    if not chain_url:
        return [{"especie": species_identifier, "evolui_de": None, "condicao": "", "estagio": 1}]
    chain = http.get(_abs(chain_url))["chain"]
    stages = []

    def walk(node: dict, parent: Optional[str], depth: int):
        name = node["species"]["name"]
        species_id = int(node["species"]["url"].rstrip("/").split("/")[-1])
        stages.append({"especie": name, "numero_pokedex": species_id, "evolui_de": parent,
                       "condicao": _condition(node.get("evolution_details", [])), "estagio": depth})
        for child in node.get("evolves_to", []):
            walk(child, name, depth + 1)

    walk(chain, None, 1)
    return stages


def type_relations(type_name: str) -> dict:
    """GET /type/{tipo} -> damage_relations"""
    return http.get(f"{settings.pokeapi_url.rstrip('/')}/type/{type_name}")["damage_relations"]


def weaknesses(types: list) -> dict:
    """Multiplicador de dano recebido de cada tipo de ataque, combinando os tipos do defensor."""
    mult = {t: 1.0 for t in TYPES}
    for t in types:
        rel = type_relations(t)
        for x in rel.get("double_damage_from", []):
            if x["name"] in mult:
                mult[x["name"]] *= 2
        for x in rel.get("half_damage_from", []):
            if x["name"] in mult:
                mult[x["name"]] *= 0.5
        for x in rel.get("no_damage_from", []):
            if x["name"] in mult:
                mult[x["name"]] = 0.0
    fmt = lambda v: int(v) if v == int(v) else v  # noqa: E731
    return {
        "tipos": types,
        "fraquezas": {t: fmt(v) for t, v in sorted(mult.items(), key=lambda kv: -kv[1]) if v > 1},
        "resistencias": {t: fmt(v) for t, v in sorted(mult.items(), key=lambda kv: kv[1]) if 0 < v < 1},
        "imunidades": [t for t, v in mult.items() if v == 0],
        "fonte": "PokéAPI /type",
    }


# ---------------- TCGdex ----------------
def tcg_cards(name: str, limit: int = 6) -> dict:
    """GET /v2/{idioma}/cards?name=... -> cartas mais recentes com imagem."""
    data = http.get(f"{settings.tcgdex_url.rstrip('/')}/{settings.tcgdex_lang}/cards", params={"name": name})
    if not isinstance(data, list):
        raise ExternalAPIError("A TCGdex devolveu um formato inesperado.")
    with_image = [c for c in data if c.get("image")]
    newest = list(reversed(with_image))[:limit]  # a TCGdex ordena da mais antiga para a mais nova
    return {
        "total_encontradas": len(data),
        "cartas": [{"id": c.get("id"), "nome": c.get("name"), "numero": c.get("localId"),
                    "imagem": c["image"] + "/low.png", "imagem_grande": c["image"] + "/high.png"}
                   for c in newest],
        "fonte": "TCGdex /cards",
    }
