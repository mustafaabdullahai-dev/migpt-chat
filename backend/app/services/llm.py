"""LLM provider abstraction + factory.

    ModelProvider
        ├── GroqProvider
        ├── MockProvider
        └── (future: Anthropic, OpenRouter...)
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_groq import ChatGroq

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("llm")


class ModelProvider(ABC):
    name: str = "base"

    @abstractmethod
    def chat_model(self, **kwargs: Any) -> BaseChatModel:
        ...


class GroqProvider(ModelProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def chat_model(self, **kwargs: Any) -> ChatGroq:
        return ChatGroq(
            api_key=self._api_key,
            model=self._model,
            temperature=kwargs.pop("temperature", 0.2),
            max_tokens=kwargs.pop("max_tokens", 4096),
            streaming=kwargs.pop("streaming", True),
            **kwargs,
        )


class MockProvider(ModelProvider):
    """Deterministic offline model so the whole app works without any API key."""

    name = "mock"

    def __init__(self, model: str = "mock-demo") -> None:
        self._model = model

    def chat_model(self, **kwargs: Any) -> BaseChatModel:
        return _MockChatModel(model=self._model, temperature=kwargs.pop("temperature", 0.2))


class _MockChatModel(BaseChatModel):
    model: str = "mock-demo"
    temperature: float = 0.2

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        query = self._last_user_text(messages)
        answer = (
            "**Demo mode active** — no API key configured.\n\n"
            f"Your question was: *{query[:400]}*\n\n"
            "Set `GROQ_API_KEY` and `DEMO_MODE=false` in `backend/.env` to enable the real Groq-powered "
            "agent with web search, calculator, research and document RAG."
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])

    @staticmethod
    def _last_user_text(messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            if msg.type == "human":
                return str(msg.content)
        return ""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return self


# ------------------------------ factory ------------------------------
_factory_cache: dict[tuple, BaseChatModel] = {}


def get_llm(
    provider: str | None = None,
    *,
    temperature: float = 0.2,
    streaming: bool = True,
    max_tokens: int = 4096,
) -> BaseChatModel:
    return _build_llm(
        provider=provider,
        model_name=None,
        temperature=temperature,
        streaming=streaming,
        max_tokens=max_tokens,
    )


def get_fast_llm(
    *,
    temperature: float = 0.0,
    streaming: bool = False,
    max_tokens: int = 4096,
) -> BaseChatModel:
    """Low-latency model for cheap auxiliary calls (routing, titles, summaries).

    Keeps the small/fast model off the final-answer streaming path so the
    big model's token budget is reserved for the user-facing response while
    aux latency stays minimal.
    """
    return _build_llm(
        provider=None,
        model_name=settings.groq_fast_model or settings.groq_model,
        temperature=temperature,
        streaming=streaming,
        max_tokens=max_tokens,
    )


def _build_llm(
    provider: str | None,
    model_name: str | None,
    *,
    temperature: float,
    streaming: bool,
    max_tokens: int,
) -> BaseChatModel:
    provider = provider or (settings.llm_provider if not settings.is_demo else "mock")
    model_name = model_name or settings.groq_model
    key = (provider, model_name, round(temperature, 2), streaming, max_tokens)
    cached = _factory_cache.get(key)
    if cached:
        return cached

    if provider == "groq":
        if not settings.groq_api_key:
            logger.warning("GROQ_API_KEY missing; falling back to mock provider", extra={"event": "llm_fallback"})
            provider = "mock"
    if provider == "mock":
        model: BaseChatModel = MockProvider().chat_model(temperature=temperature)
    elif provider == "groq":
        model = GroqProvider(settings.groq_api_key, model_name).chat_model(
            temperature=temperature, streaming=streaming, max_tokens=max_tokens
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    _factory_cache[key] = model
    return model


def provider_name() -> str:
    if settings.is_demo:
        return "mock"
    return settings.llm_provider or "groq"


def new_thread_id() -> str:
    return str(uuid.uuid4())


def call_with_retry(
    fn,
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    desc: str = "llm call",
    log: Any = None,
) -> Any:
    """Run `fn` up to `attempts` times, sleeping with backoff between failures.

    Used for small auxiliary LLM calls (titles, follow-ups, summaries) that can
    transiently hit Groq's output-token-per-minute limit. Failures are logged.
    """
    import time

    lg = log or logger
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                lg.warning(
                    "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                    desc, attempt, attempts, exc, delay,
                )
                time.sleep(delay)
    assert last is not None
    raise last