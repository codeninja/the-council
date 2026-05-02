"""Tests for multi-provider LLM backends."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from the_council.backends import (
    SUPPORTED_PROVIDERS,
    AnthropicBackend,
    LLMBackend,
    OpenAICompatibleBackend,
    make_backend,
)
from the_council.member import CouncilMember
from the_council.personas import PersonaConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_tool_handler(name: str, args: dict[str, Any]) -> str:
    return f"tool:{name}"


# ---------------------------------------------------------------------------
# make_backend factory
# ---------------------------------------------------------------------------


class TestMakeBackend:
    def test_returns_anthropic_backend(self) -> None:
        backend = make_backend("anthropic", "claude-opus-4-5")
        assert isinstance(backend, AnthropicBackend)

    def test_returns_openai_backend(self) -> None:
        backend = make_backend("openai", "gpt-4o")
        assert isinstance(backend, OpenAICompatibleBackend)

    def test_returns_openrouter_backend(self) -> None:
        backend = make_backend("openrouter", "mistralai/mistral-7b-instruct")
        assert isinstance(backend, OpenAICompatibleBackend)

    def test_returns_ollama_backend(self) -> None:
        backend = make_backend("ollama", "llama3")
        assert isinstance(backend, OpenAICompatibleBackend)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            make_backend("totally_fake_provider", "some-model")

    def test_provider_case_insensitive(self) -> None:
        backend = make_backend("Anthropic", "claude-opus-4-5")
        assert isinstance(backend, AnthropicBackend)

    def test_supported_providers_constant(self) -> None:
        assert {"anthropic", "openai", "openrouter", "ollama"} == SUPPORTED_PROVIDERS


# ---------------------------------------------------------------------------
# AnthropicBackend
# ---------------------------------------------------------------------------


class TestAnthropicBackend:
    def test_simple_response(self) -> None:
        """Backend returns text content on end_turn."""
        backend = AnthropicBackend("claude-opus-4-5", api_key="test-key")

        # Mock the Anthropic client response
        mock_block = MagicMock()
        mock_block.text = "The answer is 42."
        mock_block.type = "text"

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.stop_reason = "end_turn"

        backend._client.messages.create = MagicMock(return_value=mock_response)

        result = backend.complete("System.", [{"role": "user", "content": "Q"}], _no_tool_handler)
        assert result == "The answer is 42."

    def test_tool_use_loop(self) -> None:
        """Backend handles a tool-use turn followed by end_turn."""
        backend = AnthropicBackend("claude-opus-4-5", api_key="test-key")

        # First response: tool_use
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "read_file"
        tool_block.input = {"path": "README.md"}
        tool_block.id = "tu_01"

        first_response = MagicMock()
        first_response.content = [tool_block]
        first_response.stop_reason = "tool_use"

        # Second response: end_turn with answer
        text_block = MagicMock()
        text_block.text = "File contents noted."
        text_block.type = "text"

        second_response = MagicMock()
        second_response.content = [text_block]
        second_response.stop_reason = "end_turn"

        backend._client.messages.create = MagicMock(
            side_effect=[first_response, second_response]
        )
        handler = MagicMock(return_value="# README content")

        result = backend.complete("System.", [{"role": "user", "content": "Q"}], handler)
        assert result == "File contents noted."
        handler.assert_called_once_with("read_file", {"path": "README.md"})

    def test_unexpected_stop_reason_returns_text(self) -> None:
        """Unexpected stop_reason returns whatever text was collected."""
        backend = AnthropicBackend("claude-opus-4-5", api_key="test-key")

        text_block = MagicMock()
        text_block.text = "Partial response."
        text_block.type = "text"

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.stop_reason = "max_tokens"

        backend._client.messages.create = MagicMock(return_value=mock_response)

        result = backend.complete("System.", [{"role": "user", "content": "Q"}], _no_tool_handler)
        assert result == "Partial response."


# ---------------------------------------------------------------------------
# OpenAICompatibleBackend
# ---------------------------------------------------------------------------


class TestOpenAICompatibleBackend:
    def test_simple_response(self) -> None:
        """Backend returns content when no tool calls are present."""
        backend = OpenAICompatibleBackend("gpt-4o", api_key="test-key")

        mock_msg = MagicMock()
        mock_msg.content = "Hello from OpenAI."
        mock_msg.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        backend._client.chat.completions.create = MagicMock(return_value=mock_response)

        result = backend.complete("System.", [{"role": "user", "content": "Q"}], _no_tool_handler)
        assert result == "Hello from OpenAI."

    def test_tool_use_loop(self) -> None:
        """Backend handles a tool_calls turn followed by stop."""
        import json

        backend = OpenAICompatibleBackend("gpt-4o", api_key="test-key")

        # First response: tool_calls
        tool_call = MagicMock()
        tool_call.id = "call_01"
        tool_call.function.name = "list_files"
        tool_call.function.arguments = json.dumps({"path": "."})

        first_msg = MagicMock()
        first_msg.content = None
        first_msg.tool_calls = [tool_call]

        first_choice = MagicMock()
        first_choice.message = first_msg
        first_choice.finish_reason = "tool_calls"

        first_response = MagicMock()
        first_response.choices = [first_choice]

        # Second response: stop
        second_msg = MagicMock()
        second_msg.content = "I've listed the files."
        second_msg.tool_calls = None

        second_choice = MagicMock()
        second_choice.message = second_msg
        second_choice.finish_reason = "stop"

        second_response = MagicMock()
        second_response.choices = [second_choice]

        backend._client.chat.completions.create = MagicMock(
            side_effect=[first_response, second_response]
        )
        handler = MagicMock(return_value="file1.py\nfile2.py")

        result = backend.complete("System.", [{"role": "user", "content": "Q"}], handler)
        assert result == "I've listed the files."
        handler.assert_called_once_with("list_files", {"path": "."})

    def test_openrouter_base_url(self) -> None:
        """OpenRouter backend uses the correct base URL."""
        backend = make_backend("openrouter", "mistralai/mistral-7b-instruct", api_key="sk-or-test")
        assert isinstance(backend, OpenAICompatibleBackend)
        assert backend._client.base_url is not None
        assert "openrouter" in str(backend._client.base_url)

    def test_ollama_base_url(self) -> None:
        """Ollama backend uses localhost:11434 by default."""
        backend = make_backend("ollama", "llama3")
        assert isinstance(backend, OpenAICompatibleBackend)
        assert "11434" in str(backend._client.base_url)

    def test_ollama_custom_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OLLAMA_BASE_URL env var is respected."""
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://my-server:11434/v1")
        backend = make_backend("ollama", "llama3")
        assert "my-server" in str(backend._client.base_url)


# ---------------------------------------------------------------------------
# CouncilMember with injected backend
# ---------------------------------------------------------------------------


class TestCouncilMemberWithBackend:
    def _persona(self, provider: str = "anthropic", model: str = "test-model") -> PersonaConfig:
        return PersonaConfig(
            name="Test Elder",
            title="T",
            description="D",
            provider=provider,
            model=model,
        )

    def test_inject_backend(self) -> None:
        """A pre-built backend can be injected directly."""
        mock_backend = MagicMock(spec=LLMBackend)
        mock_backend.complete.return_value = "Mock response."
        member = CouncilMember(self._persona(), backend=mock_backend)
        result = member._call_llm("system", [{"role": "user", "content": "hi"}])
        assert result == "Mock response."
        mock_backend.complete.assert_called_once()

    def test_anthropic_member_created(self) -> None:
        """CouncilMember with provider='anthropic' creates AnthropicBackend."""
        persona = self._persona(provider="anthropic")
        member = CouncilMember(persona, api_key="test-key")
        assert isinstance(member._backend, AnthropicBackend)

    def test_openai_member_created(self) -> None:
        """CouncilMember with provider='openai' creates OpenAICompatibleBackend."""
        persona = self._persona(provider="openai", model="gpt-4o")
        member = CouncilMember(persona)
        assert isinstance(member._backend, OpenAICompatibleBackend)

    def test_openrouter_member_created(self) -> None:
        """CouncilMember with provider='openrouter' creates OpenAICompatibleBackend."""
        persona = self._persona(provider="openrouter", model="mistralai/mistral-7b-instruct")
        member = CouncilMember(persona)
        assert isinstance(member._backend, OpenAICompatibleBackend)

    def test_ollama_member_created(self) -> None:
        """CouncilMember with provider='ollama' creates OpenAICompatibleBackend."""
        persona = self._persona(provider="ollama", model="llama3")
        member = CouncilMember(persona)
        assert isinstance(member._backend, OpenAICompatibleBackend)

    def test_persona_api_key_takes_precedence(self) -> None:
        """Per-persona api_key overrides the global api_key argument."""
        persona = self._persona(provider="anthropic")
        persona.api_key = "persona-specific-key"
        member = CouncilMember(persona, api_key="global-fallback-key")
        # The Anthropic client should have been built with the persona's key
        assert isinstance(member._backend, AnthropicBackend)
        assert member._backend._client.api_key == "persona-specific-key"

    def test_unknown_provider_raises(self) -> None:
        """Creating a member with an unknown provider raises ValueError."""
        persona = self._persona(provider="badprovider")
        with pytest.raises(ValueError, match="Unknown provider"):
            CouncilMember(persona)


# ---------------------------------------------------------------------------
# PersonaConfig provider/api_key round-trip
# ---------------------------------------------------------------------------


class TestPersonaConfigProviderFields:
    def test_default_provider_is_anthropic(self) -> None:
        p = PersonaConfig(name="X", title="T", description="D")
        assert p.provider == "anthropic"
        assert p.api_key == ""

    def test_provider_in_markdown(self) -> None:
        p = PersonaConfig(name="X", title="T", description="D", provider="openai", model="gpt-4o")
        md = p.to_markdown()
        assert "**Provider:** openai" in md
        assert "**Model:** gpt-4o" in md

    def test_api_key_in_markdown_only_when_set(self) -> None:
        p_no_key = PersonaConfig(name="X", title="T", description="D")
        assert "API Key" not in p_no_key.to_markdown()

        p_with_key = PersonaConfig(name="X", title="T", description="D", api_key="sk-test")
        assert "**API Key:** sk-test" in p_with_key.to_markdown()

    def test_provider_round_trip(self) -> None:
        p = PersonaConfig(
            name="GPT Elder",
            title="AI Expert",
            description="Uses OpenAI.",
            provider="openai",
            model="gpt-4o",
        )
        restored = PersonaConfig.from_markdown(p.to_markdown())
        assert restored.provider == "openai"
        assert restored.model == "gpt-4o"
        assert restored.name == "GPT Elder"

    def test_api_key_round_trip(self) -> None:
        p = PersonaConfig(
            name="Local Elder",
            title="T",
            description="D",
            provider="ollama",
            model="llama3",
            api_key="",
        )
        restored = PersonaConfig.from_markdown(p.to_markdown())
        assert restored.provider == "ollama"
        assert restored.api_key == ""

    def test_provider_not_in_to_dict_api_key(self) -> None:
        """api_key must NOT appear in to_dict output (security)."""
        p = PersonaConfig(name="X", title="T", description="D", api_key="sk-secret")
        d = p.to_dict()
        assert "api_key" not in d
        assert d["provider"] == "anthropic"

    def test_missing_provider_in_file_defaults_to_anthropic(self) -> None:
        """Old persona files without a Provider line default to anthropic."""
        old_md = "# Old Elder\n\n**Title:** T  \n**Model:** claude-opus-4-5  \n\n## Description\n\nOld style.\n"
        p = PersonaConfig.from_markdown(old_md)
        assert p.provider == "anthropic"
