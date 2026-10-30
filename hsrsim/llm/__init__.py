"""LLM API clients (DeepSeek official OpenAI-compatible API; flash-only)."""
from hsrsim.llm.client import (
    FLASH_MODEL,
    DeepSeekChatClient,
    LLMClient,
    create_llm_client,
    normalize_deepseek_model,
)

__all__ = [
    "FLASH_MODEL",
    "LLMClient",
    "DeepSeekChatClient",
    "create_llm_client",
    "normalize_deepseek_model",
]
