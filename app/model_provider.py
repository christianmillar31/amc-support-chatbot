from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Sequence

import anthropic

from app.config import (
    ANTHROPIC_HAIKU_CACHE_READ_COST_PER_MTOK,
    ANTHROPIC_HAIKU_CACHE_WRITE_COST_PER_MTOK,
    ANTHROPIC_HAIKU_INPUT_COST_PER_MTOK,
    ANTHROPIC_HAIKU_OUTPUT_COST_PER_MTOK,
    ANTHROPIC_SONNET_CACHE_READ_COST_PER_MTOK,
    ANTHROPIC_SONNET_CACHE_WRITE_COST_PER_MTOK,
    ANTHROPIC_SONNET_INPUT_COST_PER_MTOK,
    ANTHROPIC_SONNET_OUTPUT_COST_PER_MTOK,
    ANSWER_PROVIDER,
    CHEAP_TASK_PROVIDER,
    CLAUDE_MODEL,
    LOCAL_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    QUERY_EXPANSION_MODEL,
    get_anthropic_client,
)


MessageList = list[dict[str, object]]


@dataclass
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass
class ProviderResult:
    text: str
    provider_name: str
    model_name: str
    usage: ProviderUsage
    estimated_cost_usd: float
    stop_reason: str | None = None


class ProviderStream(ABC):
    @abstractmethod
    def __iter__(self) -> Iterator[str]:
        raise NotImplementedError

    @abstractmethod
    def final_result(self) -> ProviderResult:
        raise NotImplementedError


class ModelProvider(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    def complete(
        self,
        *,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def open_stream(
        self,
        *,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> ProviderStream:
        raise NotImplementedError


def _approximate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _approximate_input_tokens(messages: MessageList, system_prompt: str) -> int:
    content_parts = [system_prompt]
    for message in messages:
        content_parts.append(str(message.get("content", "")))
    return _approximate_tokens_from_text("\n".join(content_parts))


class _AnthropicProviderStream(ProviderStream):
    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        system_prompt: str,
        messages: MessageList,
        max_tokens: int,
        temperature: float,
        cache_system_prompt: bool,
        input_cost_per_mtok: float,
        output_cost_per_mtok: float,
        cache_write_cost_per_mtok: float,
        cache_read_cost_per_mtok: float,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self._stream = get_anthropic_client().messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            system=_anthropic_system_prompt(system_prompt, cache_system_prompt),
            messages=messages,
            temperature=temperature,
        )
        self._input_cost_per_mtok = input_cost_per_mtok
        self._output_cost_per_mtok = output_cost_per_mtok
        self._cache_write_cost_per_mtok = cache_write_cost_per_mtok
        self._cache_read_cost_per_mtok = cache_read_cost_per_mtok
        self._entered = None
        self._closed = False

    def __iter__(self) -> Iterator[str]:
        if self._entered is None:
            self._entered = self._stream.__enter__()
        for text in self._entered.text_stream:
            yield text

    def final_result(self) -> ProviderResult:
        if self._entered is None:
            self._entered = self._stream.__enter__()
        try:
            final_message = self._entered.get_final_message()
            usage = _extract_anthropic_usage(final_message)
            text = "".join(block.text for block in final_message.content if block.type == "text")
            return ProviderResult(
                text=text,
                provider_name=self.provider_name,
                model_name=self.model_name,
                usage=usage,
                estimated_cost_usd=_estimate_anthropic_cost(
                    usage,
                    input_cost_per_mtok=self._input_cost_per_mtok,
                    output_cost_per_mtok=self._output_cost_per_mtok,
                    cache_write_cost_per_mtok=self._cache_write_cost_per_mtok,
                    cache_read_cost_per_mtok=self._cache_read_cost_per_mtok,
                ),
                stop_reason=getattr(final_message, "stop_reason", None),
            )
        finally:
            if not self._closed:
                self._stream.__exit__(None, None, None)
                self._closed = True


class AnthropicProvider(ModelProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        input_cost_per_mtok: float,
        output_cost_per_mtok: float,
        cache_write_cost_per_mtok: float,
        cache_read_cost_per_mtok: float,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self._input_cost_per_mtok = input_cost_per_mtok
        self._output_cost_per_mtok = output_cost_per_mtok
        self._cache_write_cost_per_mtok = cache_write_cost_per_mtok
        self._cache_read_cost_per_mtok = cache_read_cost_per_mtok

    def complete(
        self,
        *,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> ProviderResult:
        response = get_anthropic_client().messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            system=_anthropic_system_prompt(system_prompt, cache_system_prompt),
            messages=messages,
            temperature=temperature,
        )
        usage = _extract_anthropic_usage(response)
        return ProviderResult(
            text="".join(block.text for block in response.content if block.type == "text"),
            provider_name=self.provider_name,
            model_name=self.model_name,
            usage=usage,
            estimated_cost_usd=_estimate_anthropic_cost(
                usage,
                input_cost_per_mtok=self._input_cost_per_mtok,
                output_cost_per_mtok=self._output_cost_per_mtok,
                cache_write_cost_per_mtok=self._cache_write_cost_per_mtok,
                cache_read_cost_per_mtok=self._cache_read_cost_per_mtok,
            ),
            stop_reason=getattr(response, "stop_reason", None),
        )

    def open_stream(
        self,
        *,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> ProviderStream:
        return _AnthropicProviderStream(
            provider_name=self.provider_name,
            model_name=self.model_name,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_system_prompt=cache_system_prompt,
            input_cost_per_mtok=self._input_cost_per_mtok,
            output_cost_per_mtok=self._output_cost_per_mtok,
            cache_write_cost_per_mtok=self._cache_write_cost_per_mtok,
            cache_read_cost_per_mtok=self._cache_read_cost_per_mtok,
        )


class _OllamaProviderStream(ProviderStream):
    def __init__(
        self,
        *,
        model_name: str,
        base_url: str,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> None:
        from app.ollama_client import ollama_chat_stream

        self.provider_name = "ollama"
        self.model_name = model_name
        self._messages = _ollama_messages(messages, system_prompt)
        self._iterator = ollama_chat_stream(
            self._messages,
            model=model_name,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._collected: list[str] = []

    def __iter__(self) -> Iterator[str]:
        for token in self._iterator:
            self._collected.append(token)
            yield token

    def final_result(self) -> ProviderResult:
        text = "".join(self._collected)
        return ProviderResult(
            text=text,
            provider_name=self.provider_name,
            model_name=self.model_name,
            usage=ProviderUsage(
                input_tokens=_approximate_input_tokens(self._messages, ""),
                output_tokens=_approximate_tokens_from_text(text),
            ),
            estimated_cost_usd=0.0,
            stop_reason="end_turn",
        )


class OllamaProvider(ModelProvider):
    def __init__(self, *, model_name: str, base_url: str) -> None:
        self.provider_name = "ollama"
        self.model_name = model_name
        self._base_url = base_url

    def complete(
        self,
        *,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> ProviderResult:
        from app.ollama_client import ollama_chat

        ollama_messages = _ollama_messages(messages, system_prompt)
        text = ollama_chat(
            ollama_messages,
            model=self.model_name,
            base_url=self._base_url,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return ProviderResult(
            text=text,
            provider_name=self.provider_name,
            model_name=self.model_name,
            usage=ProviderUsage(
                input_tokens=_approximate_input_tokens(ollama_messages, ""),
                output_tokens=_approximate_tokens_from_text(text),
            ),
            estimated_cost_usd=0.0,
            stop_reason="end_turn",
        )

    def open_stream(
        self,
        *,
        messages: MessageList,
        system_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        cache_system_prompt: bool = False,
    ) -> ProviderStream:
        return _OllamaProviderStream(
            model_name=self.model_name,
            base_url=self._base_url,
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _anthropic_system_prompt(system_prompt: str, cache_system_prompt: bool) -> str | Sequence[dict[str, object]]:
    if not cache_system_prompt:
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _extract_anthropic_usage(response: anthropic.types.Message | object) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ProviderUsage()
    return ProviderUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )


def _estimate_anthropic_cost(
    usage: ProviderUsage,
    *,
    input_cost_per_mtok: float,
    output_cost_per_mtok: float,
    cache_write_cost_per_mtok: float,
    cache_read_cost_per_mtok: float,
) -> float:
    return round(
        (usage.input_tokens / 1_000_000) * input_cost_per_mtok
        + (usage.output_tokens / 1_000_000) * output_cost_per_mtok
        + (usage.cache_creation_input_tokens / 1_000_000) * cache_write_cost_per_mtok
        + (usage.cache_read_input_tokens / 1_000_000) * cache_read_cost_per_mtok,
        6,
    )


def _ollama_messages(messages: MessageList, system_prompt: str) -> list[dict[str, str]]:
    payload = [{"role": "system", "content": system_prompt}]
    for message in messages:
        payload.append(
            {
                "role": str(message.get("role", "user")),
                "content": str(message.get("content", "")),
            }
        )
    return payload


def get_provider(name: str | None = None) -> ModelProvider:
    provider_name = (name or ANSWER_PROVIDER).strip().lower()
    if provider_name in {"anthropic", "anthropic_sonnet", "claude"}:
        return AnthropicProvider(
            provider_name="anthropic",
            model_name=CLAUDE_MODEL,
            input_cost_per_mtok=ANTHROPIC_SONNET_INPUT_COST_PER_MTOK,
            output_cost_per_mtok=ANTHROPIC_SONNET_OUTPUT_COST_PER_MTOK,
            cache_write_cost_per_mtok=ANTHROPIC_SONNET_CACHE_WRITE_COST_PER_MTOK,
            cache_read_cost_per_mtok=ANTHROPIC_SONNET_CACHE_READ_COST_PER_MTOK,
        )
    if provider_name in {"anthropic_haiku", "haiku", "cheap"}:
        return AnthropicProvider(
            provider_name="anthropic_haiku",
            model_name=QUERY_EXPANSION_MODEL,
            input_cost_per_mtok=ANTHROPIC_HAIKU_INPUT_COST_PER_MTOK,
            output_cost_per_mtok=ANTHROPIC_HAIKU_OUTPUT_COST_PER_MTOK,
            cache_write_cost_per_mtok=ANTHROPIC_HAIKU_CACHE_WRITE_COST_PER_MTOK,
            cache_read_cost_per_mtok=ANTHROPIC_HAIKU_CACHE_READ_COST_PER_MTOK,
        )
    if provider_name in {"ollama", "local"}:
        return OllamaProvider(model_name=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    raise ValueError(f"Unsupported provider: {provider_name}")


def get_answer_provider() -> ModelProvider:
    return get_provider(ANSWER_PROVIDER)


def get_cheap_task_provider() -> ModelProvider:
    return get_provider(CHEAP_TASK_PROVIDER)


def get_local_provider() -> ModelProvider:
    return get_provider(LOCAL_PROVIDER)
