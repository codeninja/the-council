"""
LLM provider backends for council members.

Supported providers
-------------------
- ``anthropic``   – Anthropic Claude (uses the *anthropic* SDK)
- ``openai``      – OpenAI GPT models
- ``openrouter``  – OpenRouter (any model via OpenAI-compatible API)
- ``ollama``      – Local Ollama server (OpenAI-compatible API)

Any provider that exposes an OpenAI-compatible chat-completions endpoint can be
used via the ``openai`` or ``openrouter`` providers by pointing the appropriate
environment variable (``OPENROUTER_API_KEY`` / ``OLLAMA_BASE_URL``) at the right
endpoint.

Environment variables
---------------------
- ``ANTHROPIC_API_KEY``  – required for the *anthropic* provider
- ``OPENAI_API_KEY``     – required for the *openai* provider
- ``OPENROUTER_API_KEY`` – required for the *openrouter* provider
- ``OLLAMA_BASE_URL``    – optional; defaults to ``http://localhost:11434/v1``
                           (Ollama needs no API key)
"""

from __future__ import annotations

import abc
import json
import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------

#: Mapping from provider slug → environment variable that holds its API key.
#: An empty string means no API key is needed.
_PROVIDER_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": "",  # no key required
}

#: Providers whose API key lookup should fall back to the global var when the
#: per-persona key is absent.
SUPPORTED_PROVIDERS: frozenset[str] = frozenset(_PROVIDER_ENV.keys())

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

#: Provider-agnostic tool definitions (used to derive per-provider formats below).
_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the project (read-only).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file from the project root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and directories at the given path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to a directory.",
                    "default": ".",
                }
            },
            "required": [],
        },
    },
]

# Anthropic uses ``input_schema`` instead of ``parameters``.
_ANTHROPIC_TOOLS: list[dict[str, Any]] = [
    {
        "name": t["name"],
        "description": t["description"],
        "input_schema": t["parameters"],
    }
    for t in _TOOL_DEFS
]

# OpenAI-compatible format wraps each tool in ``{"type": "function", "function": {...}}``.
_OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        },
    }
    for t in _TOOL_DEFS
]

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

#: A callable ``(tool_name, tool_input) -> result_str`` supplied by the caller.
ToolHandler = Any


class LLMBackend(abc.ABC):
    """Abstract base class for an LLM provider backend."""

    @abc.abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tool_handler: ToolHandler,
    ) -> str:
        """
        Call the LLM with an agentic tool-use loop.

        Parameters
        ----------
        system:
            The system prompt.
        messages:
            The conversation so far in OpenAI/Anthropic message format
            (role/content dicts).
        tool_handler:
            ``Callable[[str, dict[str, Any]], str]`` – invoked for every tool
            call the model makes.  Must return the tool result as a plain string.

        Returns
        -------
        str
            The model's final text response after all tool calls are resolved.
        """


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------


class AnthropicBackend(LLMBackend):
    """Anthropic Claude backend (uses the ``anthropic`` SDK)."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for the 'anthropic' provider. "
                "Install it with: pip install anthropic"
            ) from exc

        self._model = model
        self._client = _anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tool_handler: ToolHandler,
    ) -> str:
        all_messages: list[dict[str, Any]] = list(messages)

        while True:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system,
                tools=_ANTHROPIC_TOOLS,  # type: ignore[arg-type]
                messages=all_messages,  # type: ignore[arg-type]
            )

            text_parts = [b.text for b in response.content if hasattr(b, "text")]  # type: ignore[union-attr]

            if response.stop_reason == "end_turn":
                return "\n".join(text_parts).strip()

            if response.stop_reason == "tool_use":
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type == "tool_use":  # type: ignore[union-attr]
                        result = tool_handler(block.name, block.input)  # type: ignore[union-attr]
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,  # type: ignore[union-attr]
                                "content": result,
                            }
                        )
                all_messages.append({"role": "assistant", "content": response.content})
                all_messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason
            return "\n".join(text_parts).strip()


# ---------------------------------------------------------------------------
# OpenAI-compatible backend (OpenAI, OpenRouter, Ollama, …)
# ---------------------------------------------------------------------------


class OpenAICompatibleBackend(LLMBackend):
    """
    Backend for any provider that exposes an OpenAI-compatible
    chat-completions endpoint.

    This covers:

    - **OpenAI** (default base URL)
    - **OpenRouter** (base URL: ``https://openrouter.ai/api/v1``)
    - **Ollama** (base URL: ``http://localhost:11434/v1``, no real API key needed)
    - Any other OpenAI-compatible proxy or gateway
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for this provider. "
                "Install it with: pip install openai"
            ) from exc

        self._model = model
        self._client = _openai.OpenAI(
            api_key=api_key or "no-key",  # Ollama/local servers accept any non-empty string
            base_url=base_url,
        )

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tool_handler: ToolHandler,
    ) -> str:
        # Prepend system message in OpenAI style
        all_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *list(messages),
        ]

        while True:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=all_messages,  # type: ignore[arg-type]
                tools=_OPENAI_TOOLS,  # type: ignore[arg-type]
                max_tokens=2048,
            )

            choice = response.choices[0]
            msg = choice.message
            text = msg.content or ""

            # No tool calls → done
            if not msg.tool_calls:
                return text.strip()

            # Append assistant turn with tool calls
            all_messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            # Process each tool call and append results
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = tool_handler(tc.function.name, args)
                all_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

            # Continue loop with updated messages


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_backend(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMBackend:
    """
    Create an :class:`LLMBackend` for the given *provider*.

    Parameters
    ----------
    provider:
        One of ``"anthropic"``, ``"openai"``, ``"openrouter"``, ``"ollama"``.
    model:
        The model name/identifier understood by the provider.
    api_key:
        Optional explicit API key.  When absent the provider-specific
        environment variable is consulted automatically.
    base_url:
        Optional override for the API base URL (useful for self-hosted
        OpenAI-compatible endpoints).  When absent the provider default is used.

    Raises
    ------
    ValueError
        If *provider* is not recognised.
    ImportError
        If the required SDK for the chosen provider is not installed.
    """
    p = provider.lower().strip()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}. "
            f"Supported providers: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )

    # Resolve API key: explicit argument > provider env var
    env_var = _PROVIDER_ENV.get(p, "")
    resolved_key: str | None = api_key or (os.environ.get(env_var) if env_var else None)

    if p == "anthropic":
        return AnthropicBackend(model, resolved_key)

    # All remaining providers use the OpenAI-compatible SDK
    if base_url is None:
        if p == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
        elif p == "ollama":
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        # openai: base_url stays None → SDK uses https://api.openai.com/v1

    if p == "ollama" and not resolved_key:
        resolved_key = "ollama"  # Ollama accepts any non-empty string

    return OpenAICompatibleBackend(model, resolved_key, base_url)
