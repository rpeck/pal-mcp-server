"""Cost-guard error messages at the MCP boundary, and the "available models" list.

A live smoke test on 2026-11-11 showed that server.handle_call_tool refused openai/gpt-6-astra-pro with the
generic "is not available with current API keys" text, and listed the refused model itself as available.
These tests pin the fix: the boundary gives the guard's reason, and "available" means callable.
"""

import asyncio
import json

import pytest

import utils.model_restrictions as model_restrictions
from providers.openrouter import OpenRouterProvider
from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tools.shared.exceptions import ToolExecutionError
from utils import cost_policy

PRESET = "openai/gpt-6-astra-pro"


@pytest.fixture
def openrouter_only(monkeypatch):
    for var in (
        cost_policy.ALLOW_ENV,
        cost_policy.CEILING_ENV,
        "OPENROUTER_ALLOWED_MODELS",
        "OPENROUTER_DISALLOWED_MODELS",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CUSTOM_API_URL",
        "DIAL_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    ModelProviderRegistry.reset_for_testing()
    model_restrictions._restriction_service = None
    ModelProviderRegistry.register_provider(ProviderType.OPENROUTER, OpenRouterProvider)
    yield
    ModelProviderRegistry.reset_for_testing()
    model_restrictions._restriction_service = None


@pytest.mark.no_mock_provider
def test_available_models_leave_out_refused_models(openrouter_only, monkeypatch):
    available = {m.lower() for m in ModelProviderRegistry.get_available_models(respect_restrictions=True)}
    assert PRESET not in available
    assert "openrouter/fusion" not in available
    assert "anthropic/claude-haiku-5.5" in available

    monkeypatch.setenv(cost_policy.ALLOW_ENV, PRESET)
    available = {m.lower() for m in ModelProviderRegistry.get_available_models(respect_restrictions=True)}
    assert PRESET in available


@pytest.mark.no_mock_provider
def test_available_models_leave_out_models_above_the_ceiling(openrouter_only, monkeypatch):
    monkeypatch.setenv(cost_policy.CEILING_ENV, "20")
    available = {m.lower() for m in ModelProviderRegistry.get_available_models(respect_restrictions=True)}
    assert "openai/gpt-6-astra" not in available  # $50 per million output tokens
    assert "anthropic/claude-haiku-5.5" in available


@pytest.mark.no_mock_provider
def test_unfiltered_listing_still_shows_refused_models(openrouter_only):
    # respect_restrictions=False is the raw catalog; the guard does not hide models there.
    available = {m.lower() for m in ModelProviderRegistry.get_available_models(respect_restrictions=False)}
    assert PRESET in available


@pytest.mark.no_mock_provider
def test_server_boundary_gives_the_cost_guard_reason(openrouter_only, tmp_path):
    import server

    with pytest.raises(ToolExecutionError) as exc_info:
        asyncio.run(
            server.handle_call_tool(
                "chat",
                {"model": PRESET, "prompt": "hi", "working_directory_absolute_path": str(tmp_path)},
            )
        )
    content = json.loads(exc_info.value.payload)["content"]
    assert cost_policy.ALLOW_ENV in content
    assert "not available with current API keys" not in content
