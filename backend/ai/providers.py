"""LLM provider abstraction.

The rest of the code depends only on the `LLMProvider` protocol (`chat` + `embed`).
The production implementation talks to any OpenAI-compatible HTTP API: OpenAI itself,
OpenRouter, vLLM, LM Studio or a local Ollama (`/v1` endpoint). Tests swap in a fake.
"""

import logging
from itertools import batched
from typing import Protocol

import openai
from django.conf import settings

from .models import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}


class LLMError(Exception):
    """Any failure talking to the model: network, auth, rate limit, bad answer."""


class AINotConfiguredError(LLMError):
    pass


class LLMProvider(Protocol):
    model: str

    def chat(self, messages: list[Message], *, temperature: float = 0.2) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleProvider:
    EMBED_BATCH = 32

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        embedding_model: str,
        timeout: float = 180,
        dimensions: int = EMBEDDING_DIMENSIONS,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.model = model
        self.embedding_model = embedding_model
        self.dimensions = dimensions
        self.client = client or openai.OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, max_retries=2
        )

    def chat(self, messages: list[Message], *, temperature: float = 0.2) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=temperature
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"Запрос к LLM не удался: {exc}") from exc
        content = response.choices[0].message.content if response.choices else None
        if not content or not content.strip():
            raise LLMError("LLM вернула пустой ответ.")
        return content.strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in batched(texts, self.EMBED_BATCH):
            try:
                response = self.client.embeddings.create(
                    model=self.embedding_model, input=list(batch), dimensions=self.dimensions
                )
            except openai.OpenAIError as exc:
                raise LLMError(f"Запрос эмбеддингов не удался: {exc}") from exc
            for item in sorted(response.data, key=lambda d: d.index):
                if len(item.embedding) != self.dimensions:
                    raise LLMError(
                        f"Модель эмбеддингов вернула {len(item.embedding)} измерений, а нужно "
                        f"{self.dimensions}. Подойдут bge-m3 или text-embedding-3-*."
                    )
                vectors.append(list(item.embedding))
        return vectors


def is_configured() -> bool:
    return bool(settings.LLM_API_KEY)


def get_provider() -> LLMProvider:
    if not is_configured():
        raise AINotConfiguredError(
            "AI не настроен: задайте LLM_API_KEY (и LLM_BASE_URL для Ollama/другого API) в .env."
        )
    return OpenAICompatibleProvider(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        embedding_model=settings.LLM_EMBEDDING_MODEL,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )
