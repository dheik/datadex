"""
Integração com a API do Gemini (SDK oficial google-genai) usando FUNCTION CALLING.

O laço funciona assim:
  1. Envia a pergunta + a instrução de sistema + a lista de funções (tools).
  2. Se o Gemini responder com function_call(s), o backend executa cada uma (ToolRunner)
     e devolve os resultados como function_response.
  3. Repete até o Gemini responder com texto (a resposta final) ou atingir o limite de rodadas.

Se um modelo falhar (cota esgotada 429, modelo indisponível 404, instabilidade), a pergunta
recomeça do zero com o próximo modelo da lista GEMINI_MODELS.
"""
import logging
import re
from typing import Optional, Protocol

from .config import settings

log = logging.getLogger("datadex.llm")


class LLMError(RuntimeError):
    """Falha ao se comunicar com o Gemini."""


class QuotaExceededError(LLMError):
    """Todos os modelos disponíveis estão com a cota esgotada (HTTP 429)."""


class Runner(Protocol):
    def reset(self) -> None: ...
    def execute(self, name: str, args: dict) -> dict: ...


def _status(exc: Exception) -> Optional[int]:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    match = re.match(r"\s*(\d{3})\b", str(exc))
    return int(match.group(1)) if match else None


class GeminiClient:
    """Cliente do Gemini com function calling manual e troca automática de modelo."""

    def __init__(self, api_key: Optional[str] = None, models: Optional[list] = None, client=None):
        from google import genai  # import tardio
        from google.genai import types

        self._types = types
        if client is not None:  # usado nos testes
            self._client = client
        else:
            key = api_key or settings.gemini_api_key
            if not key:
                raise LLMError("GEMINI_API_KEY não configurada no arquivo .env")
            self._client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    timeout=30_000,
                    retry_options=types.HttpRetryOptions(attempts=2),
                ),
            )
        self.models = models or settings.gemini_models
        self.last_model: Optional[str] = None

    def _config(self, system_instruction: str, tool, allow_tools: bool = True):
        t = self._types
        return t.GenerateContentConfig(
            temperature=0.0,                      # respostas previsíveis
            system_instruction=system_instruction,
            tools=[tool],
            thinking_config=t.ThinkingConfig(thinking_level=t.ThinkingLevel.MINIMAL),
            # desliga a execução automática do SDK: o backend decide e executa cada função
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            tool_config=t.ToolConfig(function_calling_config=t.FunctionCallingConfig(
                mode="AUTO" if allow_tools else "NONE")),
        )

    def _run_once(self, model: str, system_instruction: str, contents: list, tool, runner: Runner,
                  max_rounds: int) -> str:
        t = self._types
        config = self._config(system_instruction, tool)
        convo = list(contents)
        for _ in range(max_rounds):
            response = self._client.models.generate_content(model=model, contents=convo, config=config)
            calls = response.function_calls or []
            if not calls:
                text = (response.text or "").strip()
                if not text:
                    raise LLMError("O Gemini retornou uma resposta vazia.")
                return text
            # guarda a mensagem do modelo inteira (inclui a assinatura de raciocínio dos modelos Gemini 3)
            convo.append(response.candidates[0].content)
            parts = []
            for fc in calls:  # o Gemini pode pedir várias funções na mesma rodada
                try:
                    result = runner.execute(fc.name, dict(fc.args or {}))
                except Exception as exc:  # erro inesperado vira mensagem para a IA, não derruba a API
                    log.exception("Erro executando %s", fc.name)
                    result = {"erro": f"Falha interna ao executar {fc.name}: {type(exc).__name__}"}
                parts.append(t.Part(function_response=t.FunctionResponse(id=fc.id, name=fc.name, response=result)))
            convo.append(t.Content(role="user", parts=parts))
        # limite de rodadas atingido: pede a resposta final sem permitir novas chamadas
        response = self._client.models.generate_content(
            model=model, contents=convo, config=self._config(system_instruction, tool, allow_tools=False))
        text = (response.text or "").strip()
        if not text:
            raise LLMError("O Gemini não concluiu a resposta.")
        return text

    def run_with_tools(self, system_instruction: str, contents: list, tool, runner: Runner,
                       max_rounds: Optional[int] = None) -> str:
        """Roda o laço de function calling, trocando de modelo se o atual falhar."""
        max_rounds = max_rounds or settings.max_tool_rounds
        errors, statuses = [], []
        for model in self.models:
            runner.reset()  # recomeça do zero com o próximo modelo
            try:
                text = self._run_once(model, system_instruction, contents, tool, runner, max_rounds)
                self.last_model = model
                return text
            except Exception as exc:  # cota esgotada, modelo indisponível...: tenta o próximo da lista
                status = _status(exc)
                statuses.append(status)
                log.warning("Falha no modelo %s (%s): %s", model, status, str(exc)[:200])
                errors.append(f"{model}: {status or type(exc).__name__}")
        if 429 in statuses and all(s in (429, 404) for s in statuses):
            raise QuotaExceededError(
                "A cota gratuita diária do Gemini acabou em todos os modelos configurados. "
                "Perguntas já feitas continuam funcionando pelo cache. Tente de novo amanhã, "
                "use outra chave ou ative o faturamento no Google AI Studio.")
        raise LLMError("Não foi possível obter resposta do Gemini (" + "; ".join(errors) + ").")
