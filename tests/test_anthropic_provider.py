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
        assert caps["claude-opus-5-5"].intelligence_score == 20
        assert caps["claude-opus-5"].intelligence_score == 19
        assert caps["claude-fable-5"].intelligence_score == 19
        assert caps["claude-fable-5-1"].intelligence_score == 20
        assert caps["claude-sonnet-5-5"].intelligence_score == 20
        assert caps["claude-sonnet-5"].intelligence_score == 15
        assert caps["claude-haiku-4-5-20251001"].intelligence_score == 10
        assert caps["claude-opus-5"].context_window == 1_000_000
        assert caps["claude-opus-5"].supports_extended_thinking
        assert caps["claude-opus-5"].allow_code_generation

    def test_alias_resolution(self):
        p = _provider()
        # Bare "opus" points at the newest Opus (5.5); "opus-5" still addresses Opus 5.
        assert p._resolve_model_name("opus") == "claude-opus-5-5"
        assert p._resolve_model_name("opus-5.5") == "claude-opus-5-5"
        assert p._resolve_model_name("opus-5") == "claude-opus-5"
        # Bare "fable" now points at the newest Fable (5.1); "fable-5" still addresses Fable 5.
        assert p._resolve_model_name("fable") == "claude-fable-5-1"
        assert p._resolve_model_name("fable-5.1") == "claude-fable-5-1"
        assert p._resolve_model_name("fable-5") == "claude-fable-5"
        # Bare "sonnet" points at the newest Sonnet (5.5); "sonnet-5" still addresses Sonnet 5.
        assert p._resolve_model_name("sonnet") == "claude-sonnet-5-5"
        assert p._resolve_model_name("sonnet-5") == "claude-sonnet-5"
        # Bare "haiku" points at the newest Haiku (5.5); "haiku-4.5" still addresses Haiku 4.5.
        assert p._resolve_model_name("haiku") == "claude-haiku-5-5"
        assert p._resolve_model_name("haiku-4.5") == "claude-haiku-4-5-20251001"
        assert p._resolve_model_name("claude-haiku-4.5") == "claude-haiku-4-5-20251001"
        # Case-insensitive
        assert p._resolve_model_name("OPUS") == "claude-opus-5-5"

    def test_preferred_model_by_category(self):
        p = _provider()
        allowed = list(p.get_all_model_capabilities())
        # Opus 5.5 (AA 58), Sonnet 5.5 (AA 56) and Fable 5.1 (AA 53) all sit at 20, the top of the 1-20
        # scale. The raw aa_index orders the tie, so Opus 5.5 wins on its Index value; without it, the
        # name tie-break would pick Sonnet 5.5.
        assert p.get_preferred_model(ToolModelCategory.EXTENDED_REASONING, allowed) == "claude-opus-5-5"
        assert p.get_preferred_model(ToolModelCategory.BALANCED, allowed) == "claude-opus-5-5"
        assert p.get_preferred_model(ToolModelCategory.FAST_RESPONSE, allowed) == "claude-haiku-5-5"

    def test_aa_index_orders_the_top_score_tie(self):
        p = _provider()
        top = ["claude-fable-5-1", "claude-sonnet-5-5", "claude-opus-5-5"]
        assert p.get_preferred_model(ToolModelCategory.BALANCED, top) == "claude-opus-5-5"
        assert p.get_preferred_model(ToolModelCategory.BALANCED, top[:2]) == "claude-sonnet-5-5"

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
        # Requests are streamed (messages.stream(...) as a context manager, then get_final_message()).
        p = _provider()
        mock_client = MagicMock()
        stream = mock_client.messages.stream.return_value.__enter__.return_value
        stream.get_final_message.return_value = _fake_response()
        p._client = mock_client
        return p, mock_client

    @staticmethod
    def _sent(client):
        return client.messages.stream.call_args.kwargs

    def test_current_model_uses_adaptive_thinking_and_effort(self):
        p, client = self._provider_with_mock()
        resp = p.generate_content(
            prompt="hi", model_name="opus", system_prompt="sys", temperature=0.2, thinking_mode="high"
        )
        assert resp.content == "hello"
        assert resp.usage["total_tokens"] == 15
        assert resp.provider == ProviderType.ANTHROPIC
        assert resp.metadata["reasoning_effort"] == "high"
        kwargs = self._sent(client)
        assert kwargs["model"] == "claude-opus-5-5"
        assert kwargs["system"] == "sys"
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["extra_body"] == {"output_config": {"effort": "high"}}
        # Current models reject sampling parameters and fixed thinking budgets with HTTP 400.
        assert "temperature" not in kwargs
        assert "budget_tokens" not in kwargs["thinking"]
        assert kwargs["max_tokens"] == 128000
        client.messages.create.assert_not_called()

    def test_effort_is_clamped_to_the_model_levels(self):
        p, client = self._provider_with_mock()
        p.generate_content(prompt="hi", model_name="sonnet", thinking_mode="minimal")
        assert self._sent(client)["extra_body"]["output_config"] == {"effort": "low"}
        p.generate_content(prompt="hi", model_name="sonnet", thinking_mode="max")
        assert self._sent(client)["extra_body"]["output_config"] == {"effort": "max"}
        p.generate_content(prompt="hi", model_name="sonnet", thinking_mode=None)
        assert self._sent(client)["extra_body"]["output_config"] == {"effort": "medium"}

    def test_haiku_keeps_the_budget_path(self):
        p, client = self._provider_with_mock()
        resp = p.generate_content(prompt="hi", model_name="haiku-4.5", temperature=0.2, thinking_mode="high")
        kwargs = self._sent(client)
        assert resp.metadata["reasoning_effort"] is None
        assert "extra_body" not in kwargs
        # Thinking on -> temperature forced to 1.0, budget >= Anthropic floor
        assert kwargs["temperature"] == 1.0
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] >= 1024
        assert kwargs["thinking"]["budget_tokens"] < kwargs["max_tokens"]

    def test_haiku_minimal_thinking_disables_reasoning(self):
        p, client = self._provider_with_mock()
        p.generate_content(prompt="hi", model_name="haiku-4.5", temperature=0.3, thinking_mode="minimal")
        kwargs = self._sent(client)
        # Minimal maps below Anthropic's floor -> no thinking, caller temperature preserved
        assert "thinking" not in kwargs
        assert kwargs["temperature"] == 0.3
        assert kwargs["max_tokens"] == 64000  # haiku's advertised ceiling

    def test_haiku_thinking_budget_preserves_output_headroom(self):
        # A caller asking for a small visible output while requesting high thinking must still get
        # its output room on top of the thinking budget (Anthropic counts thinking against max_tokens).
        p, client = self._provider_with_mock()
        p.generate_content(prompt="hi", model_name="haiku-4.5", max_output_tokens=2000, thinking_mode="high")
        kwargs = self._sent(client)
        budget = kwargs["thinking"]["budget_tokens"]
        assert kwargs["max_tokens"] - budget >= 2000
