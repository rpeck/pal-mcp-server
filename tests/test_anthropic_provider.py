"""Unit tests for the native Anthropic (Claude) provider."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from providers.anthropic import AnthropicModelProvider
from providers.shared import ProviderType
from tools.models import ToolModelCategory


def _provider():
    return AnthropicModelProvider("test-key")


class TestAnthropicCatalog:
    def test_provider_type(self):
        assert _provider().get_provider_type() == ProviderType.ANTHROPIC

    def test_models_and_scores(self):
        caps = _provider().get_all_model_capabilities()
        assert caps["claude-opus-5"].intelligence_score == 20
        assert caps["claude-fable-5"].intelligence_score == 20
        assert caps["claude-sonnet-5"].intelligence_score == 18
        assert caps["claude-haiku-4-5-20251001"].intelligence_score == 12
        assert caps["claude-opus-5"].context_window == 1_000_000
        assert caps["claude-opus-5"].supports_extended_thinking
        assert caps["claude-opus-5"].allow_code_generation

    def test_alias_resolution(self):
        p = _provider()
        assert p._resolve_model_name("opus") == "claude-opus-5"
        assert p._resolve_model_name("fable") == "claude-fable-5"
        assert p._resolve_model_name("sonnet") == "claude-sonnet-5"
        assert p._resolve_model_name("haiku") == "claude-haiku-4-5-20251001"
        assert p._resolve_model_name("claude-haiku-4.5") == "claude-haiku-4-5-20251001"
        # Case-insensitive
        assert p._resolve_model_name("OPUS") == "claude-opus-5"

    def test_preferred_model_by_category(self):
        p = _provider()
        allowed = list(p.get_all_model_capabilities())
        # Highest raw score wins even though effective rank saturates at 100 for opus/fable/sonnet.
        assert p.get_preferred_model(ToolModelCategory.EXTENDED_REASONING, allowed) == "claude-opus-5"
        assert p.get_preferred_model(ToolModelCategory.BALANCED, allowed) == "claude-opus-5"
        assert p.get_preferred_model(ToolModelCategory.FAST_RESPONSE, allowed) == "claude-haiku-4-5-20251001"

    def test_preferred_model_handles_unknown_allowed(self):
        # allowed_models with no canonical entries must not raise (regression: KeyError on
        # capability_map[m] in the EXTENDED_REASONING filter). It falls through to a best-effort
        # return like the sibling gemini provider — the point is that it does not crash.
        p = _provider()
        result = p.get_preferred_model(ToolModelCategory.EXTENDED_REASONING, ["nonexistent-model"])
        assert result == "nonexistent-model"


def _fake_response(text="hello", input_tokens=10, output_tokens=5):
    block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=[block], usage=usage, stop_reason="end_turn")


class TestAnthropicGenerateContent:
    def _provider_with_mock(self):
        p = _provider()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_response()
        p._client = mock_client
        return p, mock_client

    def test_thinking_enabled_forces_temperature_and_sets_budget(self):
        p, client = self._provider_with_mock()
        resp = p.generate_content(
            prompt="hi", model_name="opus", system_prompt="sys", temperature=0.2, thinking_mode="high"
        )
        assert resp.content == "hello"
        assert resp.usage["total_tokens"] == 15
        assert resp.provider == ProviderType.ANTHROPIC
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-opus-5"
        assert kwargs["max_tokens"] > 0
        assert kwargs["system"] == "sys"
        # Thinking on -> temperature forced to 1.0, budget >= Anthropic floor
        assert kwargs["temperature"] == 1.0
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] >= 1024
        assert kwargs["thinking"]["budget_tokens"] < kwargs["max_tokens"]

    def test_minimal_thinking_disables_reasoning(self):
        p, client = self._provider_with_mock()
        p.generate_content(prompt="hi", model_name="opus", temperature=0.3, thinking_mode="minimal")
        kwargs = client.messages.create.call_args.kwargs
        # Minimal maps below Anthropic's floor -> no thinking, caller temperature preserved
        assert "thinking" not in kwargs
        assert kwargs["temperature"] == 0.3

    def test_max_tokens_always_sent(self):
        p, client = self._provider_with_mock()
        p.generate_content(prompt="hi", model_name="haiku", thinking_mode="minimal")
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 64000  # haiku's advertised ceiling

    def test_thinking_budget_preserves_output_headroom(self):
        # A caller asking for a small visible output while requesting high thinking must still get
        # its output room on top of the thinking budget (Anthropic counts thinking against max_tokens).
        p, client = self._provider_with_mock()
        p.generate_content(prompt="hi", model_name="opus", max_output_tokens=2000, thinking_mode="high")
        kwargs = client.messages.create.call_args.kwargs
        budget = kwargs["thinking"]["budget_tokens"]
        assert kwargs["max_tokens"] - budget >= 2000
