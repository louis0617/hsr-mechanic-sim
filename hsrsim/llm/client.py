"""Unified chat-completions client; DeepSeek uses official OpenAI-compatible API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Protocol

from hsrsim.llm.env import load_dotenv


class LLMClient(Protocol):
    """Single-turn chat completion (M5 JSON / M8 Python code)."""

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 8000,
        json_mode: bool = False,
        system: str | None = None,
    ) -> str: ...


@dataclass
class DeepSeekChatClient:
    """DeepSeek API via OpenAI SDK.

    Docs: https://api-docs.deepseek.com/zh-cn/
    - base_url: https://api.deepseek.com
    - model: deepseek-v4-pro (thinking enabled by default)
    - thinking: extra_body={"thinking": {"type": "enabled"}}
    - json: response_format={"type": "json_object"} (prompt must mention json)
    """

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    thinking: bool = True
    reasoning_effort: Literal["high", "max"] = "high"

    _client: object | None = None

    @classmethod
    def from_env(cls) -> DeepSeekChatClient:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. "
                "Apply at https://platform.deepseek.com/ and export the key."
            )
        thinking_env = os.getenv("DEEPSEEK_THINKING", "enabled").strip().lower()
        thinking = thinking_env not in ("0", "false", "disabled", "off")
        effort = os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()
        if effort not in ("high", "max"):
            effort = "high"
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip(),
            thinking=thinking,
            reasoning_effort=effort,  # type: ignore[arg-type]
        )

    @property
    def client(self):
        if self._client is None:
            import httpx
            from openai import OpenAI

            read_s = float(os.getenv("DEEPSEEK_TIMEOUT", "300"))
            connect_s = float(os.getenv("DEEPSEEK_CONNECT_TIMEOUT", "60"))
            max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "3"))
            timeout = httpx.Timeout(connect=connect_s, read=read_s, write=30.0, pool=30.0)
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=timeout,
                max_retries=max_retries,
            )
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 8000,
        json_mode: bool = False,
        system: str | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.thinking:
            kwargs["reasoning_effort"] = self.reasoning_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        content = message.content
        if not content or not str(content).strip():
            reasoning = getattr(message, "reasoning_content", None) or ""
            usage = getattr(response, "usage", None)
            raise RuntimeError(
                "DeepSeek returned empty content "
                f"(finish_reason={choice.finish_reason!r}, "
                f"reasoning_chars={len(reasoning)}, "
                f"max_tokens={max_tokens}, usage={usage}). "
                "Increase max_tokens when thinking is enabled, or pass --no-thinking."
            )
        return str(content)


@dataclass
class AnthropicChatClient:
    """Optional Anthropic backend (legacy skeleton default)."""

    api_key: str
    model: str = "claude-sonnet-4-20250514"
    _client: object | None = None

    @classmethod
    def from_env(cls) -> AnthropicChatClient:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        return cls(
            api_key=api_key,
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip(),
        )

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 8000,
        json_mode: bool = False,
        system: str | None = None,
    ) -> str:
        user_content = prompt
        if json_mode and "json" not in prompt.lower():
            user_content = prompt + "\n\nRespond with valid JSON only."
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text


def create_llm_client(
    provider: str | None = None,
    *,
    thinking: bool | None = None,
) -> LLMClient:
    """Factory: provider from arg or PCGRLLM_LLM_PROVIDER (default deepseek)."""
    load_dotenv()
    name = (provider or os.getenv("PCGRLLM_LLM_PROVIDER", "deepseek")).strip().lower()
    if name in ("deepseek", "ds"):
        client = DeepSeekChatClient.from_env()
        if thinking is not None:
            client.thinking = thinking
        return client
    if name in ("anthropic", "claude"):
        return AnthropicChatClient.from_env()
    raise ValueError(f"Unknown LLM provider: {name!r}. Use 'deepseek' or 'anthropic'.")
