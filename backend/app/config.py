"""Configurações da aplicação, lidas do arquivo .env."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _list(value: str) -> list:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
    # Modelos do Gemini, em ordem. Cada modelo tem sua própria cota gratuita diária,
    # então, quando um esgota (erro 429) ou não existe (404), a API passa para o próximo.
    gemini_models: list = field(default_factory=lambda: _list(os.getenv(
        "GEMINI_MODELS", "gemini-3.6-flash,gemini-3.8-flash")))
    # Quantas rodadas de function calling o Gemini pode fazer em uma pergunta
    max_tool_rounds: int = int(os.getenv("MAX_TOOL_ROUNDS", "6"))

    # APIs externas consultadas ao vivo pelas funções
    pokeapi_url: str = os.getenv("POKEAPI_URL", "https://pokeapi.co/api/v2")
    tcgdex_url: str = os.getenv("TCGDEX_URL", "https://api.tcgdex.net/v2")
    tcgdex_lang: str = os.getenv("TCGDEX_LANG", "en")
    external_timeout_s: float = float(os.getenv("EXTERNAL_TIMEOUT_S", "8"))

    database_path: str = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "data", "pokemon.db"))
    max_question_length: int = int(os.getenv("MAX_QUESTION_LENGTH", "300"))
    max_rows: int = int(os.getenv("MAX_ROWS", "50"))
    query_timeout_ms: int = int(os.getenv("QUERY_TIMEOUT_MS", "2000"))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
    cors_origins: list = field(default_factory=lambda: _list(os.getenv("CORS_ORIGINS", "*")))
    # Cache salvo em disco: perguntas já respondidas não gastam cota, nem depois de reiniciar a API
    cache_path: str = os.getenv("CACHE_PATH", os.path.join(BASE_DIR, "data", "cache.json"))


settings = Settings()
