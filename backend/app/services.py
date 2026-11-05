"""
Regras de negócio do DataDex.

Fluxo de uma pergunta:
  validar pergunta -> (cache?) -> Gemini decide quais funções chamar (function calling)
  -> backend executa as funções (banco local, PokéAPI, TCGdex) -> Gemini escreve a resposta
  usando SOMENTE os resultados -> backend monta cards/evolução/fraquezas/cartas -> histórico.
"""
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Optional

from google.genai import types as gtypes

from . import database
from .config import settings
from .prompts import SYSTEM_INSTRUCTION
from .tools import TOOL, ToolRunner

log = logging.getLogger("datadex.service")
CACHE_LIMIT = 200
OFF_TOPIC_TAG = "[FORA_DO_TEMA]"


class ValidationError(ValueError):
    """Pergunta inválida (regra de negócio)."""


class NotAnswerableError(ValueError):
    """A pergunta não pode ser respondida com os dados disponíveis."""


def normalize_question(question: Optional[str]) -> str:
    if question is None:
        raise ValidationError("Envie uma pergunta.")
    q = unicodedata.normalize("NFC", re.sub(r"\s+", " ", str(question))).strip()
    if len(q) < 3:
        raise ValidationError("A pergunta é muito curta. Ex.: 'Qual o Pokémon mais rápido?'")
    if len(q) > settings.max_question_length:
        raise ValidationError(f"A pergunta deve ter no máximo {settings.max_question_length} caracteres.")
    if not re.search(r"[A-Za-zÀ-ÿ]", q):
        raise ValidationError("A pergunta precisa conter texto.")
    return q


class DataDexService:
    def __init__(self, llm, cache_path: Optional[str] = None):
        self.llm = llm
        self.system_instruction = SYSTEM_INSTRUCTION.format(schema=database.get_schema_description())
        self.cache_path = cache_path
        self._cache: "OrderedDict[str, dict]" = self._load_cache()
        self._sessions: dict = {}
        self.history: deque = deque(maxlen=50)

    # ---------- cache persistente ----------
    def _load_cache(self) -> "OrderedDict[str, dict]":
        if not self.cache_path or not os.path.exists(self.cache_path):
            return OrderedDict()
        try:
            with open(self.cache_path, encoding="utf-8") as fh:
                return OrderedDict(json.load(fh))
        except (OSError, ValueError) as exc:
            log.warning("Cache ignorado (%s)", exc)
            return OrderedDict()

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._cache, fh, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            log.warning("Não foi possível salvar o cache (%s)", exc)

    # ---------- conversa (perguntas de continuação) ----------
    def _contents(self, session_id: Optional[str], question: str) -> list:
        """Histórico curto da sessão + pergunta atual, no formato do Gemini."""
        contents = []
        for turn in self._sessions.get(session_id) or []:
            contents.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=turn["question"])]))
            contents.append(gtypes.Content(role="model", parts=[gtypes.Part.from_text(text=turn["answer"])]))
        contents.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=question)]))
        return contents

    def _remember(self, session_id: Optional[str], question: str, answer: str) -> None:
        if session_id:
            self._sessions.setdefault(session_id, deque(maxlen=3)).append({"question": question, "answer": answer})

    # ---------- caso de uso principal ----------
    def ask(self, question: str, session_id: Optional[str] = None) -> dict:
        started = time.perf_counter()
        q = normalize_question(question)
        has_history = bool(self._sessions.get(session_id))

        cache_key = q.lower()
        if not has_history and cache_key in self._cache:
            cached = {**self._cache[cache_key], "cached": True}
            self._remember(session_id, q, cached["answer"])
            self._log(q, cached)
            return cached

        runner = ToolRunner()
        text = self.llm.run_with_tools(self.system_instruction, self._contents(session_id, q), TOOL, runner)

        # Regra de negócio: resposta sem nenhuma função bem-sucedida não tem fonte -> não é mostrada
        if text.startswith(OFF_TOPIC_TAG):
            raise NotAnswerableError(text[len(OFF_TOPIC_TAG):].strip()
                                     or "Só consigo responder perguntas sobre Pokémon.")
        if not any(c["ok"] for c in runner.calls):
            raise NotAnswerableError("Não encontrei dados para responder a essa pergunta. "
                                     "Tente perguntar sobre tipos, status, evoluções, fraquezas ou cartas.")

        pokemon = database.fetch_pokemon_by_ids(runner.pokemon_ids[: settings.max_rows])
        for p in pokemon:
            p["query_values"] = runner.query_values.get(p["id"], {})

        response = {
            "id": str(uuid.uuid4()),
            "question": q,
            "answer": text,
            "explanation": runner.explanation,
            "sql": runner.sql,
            "result_type": "pokemon" if pokemon else ("table" if runner.table else "info"),
            "pokemon": pokemon,
            "table": runner.table,
            "description": runner.description,
            "evolution": runner.evolution,
            "weaknesses": runner.weaknesses,
            "cards": runner.cards,
            "tool_calls": runner.calls,
            "row_count": runner.row_count,
            "truncated": runner.truncated,
            "model": getattr(self.llm, "last_model", None),
            "cached": False,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
        self._cache[cache_key] = response
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)
        self._save_cache()
        self._remember(session_id, q, text)
        self._log(q, response)
        return response

    def _log(self, question: str, response: dict) -> None:
        self.history.appendleft({
            "question": question,
            "tools": [c["name"] for c in response.get("tool_calls", [])],
            "sql": response.get("sql"),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
