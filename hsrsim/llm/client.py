"""Unified chat-completions client; DeepSeek uses official OpenAI-compatible API.

Project policy: only ``deepseek-flash`` (served as DeepSeek-V4.1-Flash-1017) may be
called. Other DeepSeek model ids are remapped; non-DeepSeek providers are refused.
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from typing import Literal, Protocol

from hsrsim.llm.env import load_dotenv

FLASH_MODEL = "deepseek-flash"
# Official aliases that still bill / route as Flash.
_FLASH_ALIASES = frozenset(
    {
        "deepseek-flash",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "deepseek-v4.1-flash",
        "deepseek-v4.1-flash-1017",
    }
)


def normalize_deepseek_model(model: str | None) -> str:
    """Force every DeepSeek call onto ``deepseek-flash``."""
    raw = (model or FLASH_MODEL).strip()
    key = raw.lower()
    if key in _FLASH_ALIASES or key == FLASH_MODEL:
        if key != FLASH_MODEL:
            warnings.warn(
                f"DEEPSEEK_MODEL={raw!r} remapped to {FLASH_MODEL!r} (project flash-only policy)",
                stacklevel=2,
            )
        return FLASH_MODEL
    warnings.warn(
        f"DEEPSEEK_MODEL={raw!r} is not allowed; forcing {FLASH_MODEL!r} (project flash-only policy)",
        stacklevel=2,
    )
    return FLASH_MODEL


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
    - model: always deepseek-flash (V4.1-Flash-1017)
    - thinking: extra_body={"thinking": {"type": "enabled"}}
    - json: response_format={"type": "json_object"} (prompt must mention json)
    """

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = FLASH_MODEL
    thinking: bool = True
    reasoning_effort: Literal["high", "max"] = "high"

    _client: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.model = normalize_deepseek_model(self.model)

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
            model=normalize_deepseek_model(os.getenv("DEEPSEEK_MODEL", FLASH_MODEL)),
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
        max_tokens: int | None = 8000,
        json_mode: bool = False,
        system: str | None = None,
    ) -> str:
        self.model = normalize_deepseek_model(self.model)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.thinking:
            kwargs["reasoning_effort"] = self.reasoning_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        response = self.client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None and hasattr(usage, "model_dump"):
            self.last_usage = usage.model_dump()
        elif usage is not None and hasattr(usage, "dict"):
            self.last_usage = usage.dict()
        else:
            self.last_usage = None
        choice = response.choices[0]
        message = choice.message
        content = message.content
        reasoning = getattr(message, "reasoning_content", None) or ""
        self.last_finish_reason = choice.finish_reason
        self.last_reasoning_chars = len(reasoning)
        if not content or not str(content).strip():
            usage = getattr(response, "usage", None)
            raise RuntimeError(
                "DeepSeek returned empty content "
                f"(finish_reason={choice.finish_reason!r}, "
                f"reasoning_chars={len(reasoning)}, "
                f"max_tokens={max_tokens}, usage={usage}). "
                "Increase max_tokens when thinking is enabled, or pass --no-thinking."
            )
        return str(content)


def create_llm_client(
    provider: str | None = None,
    *,
    thinking: bool | None = None,
) -> LLMClient:
    """Factory: always returns DeepSeek flash. Non-DeepSeek providers are refused."""
    load_dotenv()
    name = (provider or os.getenv("PCGRLLM_LLM_PROVIDER", "deepseek")).strip().lower()
    if name not in ("deepseek", "ds", "flash", "deepseek-flash"):
        raise ValueError(
            f"LLM provider {name!r} is disabled; this project only allows deepseek-flash. "
            "Set PCGRLLM_LLM_PROVIDER=deepseek."
        )
    client = DeepSeekChatClient.from_env()
    if thinking is not None:
        client.thinking = thinking
    client.model = FLASH_MODEL
    return client
