"""Cost guard (utils/cost_policy.py): opt-in models and the output-price ceiling.

Background: ``auto_selectable: false`` keeps an expensive preset out of auto mode, but an agent can still name
it. On 2026-11-09/10 a coding agent called GPT-6 Astra Pro by name through OpenRouter and spent about $190.
These tests pin the guard that refuses such a call unless the machine opts in.
"""

import json
import re
from pathlib import Path

import pytest

import utils.model_restrictions
from providers.openrouter import OpenRouterProvider
from providers.shared import ProviderType
from providers.shared.model_capabilities import ModelCapabilities
from utils import cost_policy

CONF = Path(__file__).resolve().parents[1] / "conf"
PRO = re.compile(r"(^|/)(o\d+-pro|gpt-[\d.]+(-[a-z]+)?-pro)$")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        cost_policy.ALLOW_ENV,
        cost_policy.CEILING_ENV,
        "OPENROUTER_ALLOWED_MODELS",
        "OPENROUTER_DISALLOWED_MODELS",
    ):
        monkeypatch.delenv(var, raising=False)
    utils.model_restrictions._restriction_service = None
    yield
    utils.model_restrictions._restriction_service = None


def caps(name="vendor/model", **kw):
    return ModelCapabilities(provider=ProviderType.OPENROUTER, model_name=name, friendly_name=name, **kw)


def models(conf_name):
    return json.loads((CONF / conf_name).read_text())["models"]


# --- the catalog -----------------------------------------------------------------------------------


@pytest.mark.parametrize("conf_name", ["openai_models.json", "openrouter_models.json"])
def test_every_openai_pro_model_requires_opt_in(conf_name):
    pro = [m for m in models(conf_name) if PRO.search(m["model_name"])]
    assert pro, f"{conf_name}: expected some -pro models"
    missing = [m["model_name"] for m in pro if m.get("requires_opt_in") is not True]
    assert not missing, f"{conf_name}: -pro models must set requires_opt_in: {missing}"


def test_every_fusion_router_requires_opt_in():
    fusion = [m for m in models("openrouter_models.json") if m["model_name"].startswith("openrouter/fusion")]
    assert len(fusion) == 3
    assert all(m.get("requires_opt_in") is True for m in fusion)


def test_openrouter_prices_are_well_formed():
    priced = 0
    for m in models("openrouter_models.json"):
        has_in, has_out = "input_price_per_m" in m, "output_price_per_m" in m
        assert has_in == has_out, f"{m['model_name']}: store both prices or neither"
        if has_out:
            priced += 1
            for key in ("input_price_per_m", "output_price_per_m"):
                assert isinstance(m[key], (int, float)) and m[key] >= 0, f"{m['model_name']}: bad {key}"
    assert priced >= 50, "scripts/update_openrouter_prices.py should price most OpenRouter entries"


# --- opt-in ----------------------------------------------------------------------------------------


def test_opt_in_model_refused_by_default():
    reason = cost_policy.refusal_reason(caps("openai/gpt-6-astra-pro", requires_opt_in=True))
    assert reason and cost_policy.ALLOW_ENV in reason


@pytest.mark.parametrize("allow", ["openai/gpt-6-astra-pro", "OPENAI/GPT-6-ASTRA-PRO", "astra-pro", "all", " x , all "])
def test_opt_in_model_allowed_by_name_alias_or_all(monkeypatch, allow):
    monkeypatch.setenv(cost_policy.ALLOW_ENV, allow)
    c = caps("openai/gpt-6-astra-pro", requires_opt_in=True, aliases=["astra-pro"])
    assert cost_policy.refusal_reason(c) is None


def test_opt_in_for_one_model_does_not_allow_another(monkeypatch):
    monkeypatch.setenv(cost_policy.ALLOW_ENV, "openai/gpt-6.1-sol-pro")
    assert cost_policy.is_refused(caps("openai/gpt-6-astra-pro", requires_opt_in=True))


def test_requested_name_counts_for_opt_in(monkeypatch):
    monkeypatch.setenv(cost_policy.ALLOW_ENV, "my-alias")
    assert not cost_policy.is_refused(caps("x/pro", requires_opt_in=True), "my-alias")


# --- price ceiling ---------------------------------------------------------------------------------


def test_no_ceiling_by_default():
    assert cost_policy.output_price_ceiling() is None
    assert not cost_policy.is_refused(caps(output_price_per_m=500.0, input_price_per_m=100.0))


def test_ceiling_refuses_only_models_above_it(monkeypatch):
    monkeypatch.setenv(cost_policy.CEILING_ENV, "20")
    above = caps("a/expensive", input_price_per_m=10.0, output_price_per_m=50.0)
    at = caps("a/at", input_price_per_m=4.0, output_price_per_m=20.0)
    below = caps("a/cheap", input_price_per_m=0.1, output_price_per_m=0.5)
    unknown = caps("a/unknown")
    reason = cost_policy.refusal_reason(above)
    assert reason and "$50" in reason and cost_policy.CEILING_ENV in reason
    assert not cost_policy.is_refused(at)
    assert not cost_policy.is_refused(below)
    assert not cost_policy.is_refused(unknown), "a model with no stored price is not checked"


def test_opt_in_exempts_a_model_from_the_ceiling(monkeypatch):
    monkeypatch.setenv(cost_policy.CEILING_ENV, "20")
    monkeypatch.setenv(cost_policy.ALLOW_ENV, "a/expensive")
    assert not cost_policy.is_refused(caps("a/expensive", output_price_per_m=50.0, input_price_per_m=10.0))


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_invalid_ceiling_is_ignored(monkeypatch, bad):
    monkeypatch.setenv(cost_policy.CEILING_ENV, bad)
    assert cost_policy.output_price_ceiling() is None


# --- the provider enforces it, for explicit calls ---------------------------------------------------


def test_openrouter_provider_refuses_named_pro_preset_without_opt_in():
    prov = OpenRouterProvider(api_key="test-key")
    with pytest.raises(ValueError, match=cost_policy.ALLOW_ENV):
        prov.get_capabilities("openai/gpt-6-astra-pro")


def test_openrouter_provider_allows_named_pro_preset_with_opt_in(monkeypatch):
    monkeypatch.setenv(cost_policy.ALLOW_ENV, "openai/gpt-6-astra-pro")
    prov = OpenRouterProvider(api_key="test-key")
    assert prov.get_capabilities("openai/gpt-6-astra-pro").model_name == "openai/gpt-6-astra-pro"


def test_openrouter_provider_applies_the_ceiling(monkeypatch):
    prov = OpenRouterProvider(api_key="test-key")
    astra = prov.describe_model("openai/gpt-6-astra")
    assert astra is not None and astra.output_price_per_m is not None
    monkeypatch.setenv(cost_policy.CEILING_ENV, str(astra.output_price_per_m - 1))
    with pytest.raises(ValueError, match=cost_policy.CEILING_ENV):
        prov.get_capabilities("openai/gpt-6-astra")
    haiku = prov.describe_model("anthropic/claude-haiku-5.5")
    assert haiku is not None and haiku.output_price_per_m < astra.output_price_per_m - 1
    assert prov.get_capabilities("anthropic/claude-haiku-5.5")


def test_generate_content_reports_the_cost_guard_reason():
    # The caller must learn why and how to opt in, not get a generic "not in allowed models list".
    prov = OpenRouterProvider(api_key="test-key")
    with pytest.raises(ValueError) as excinfo:
        prov.generate_content(prompt="hi", model_name="openai/gpt-6-astra-pro", temperature=1.0)
    assert cost_policy.ALLOW_ENV in str(excinfo.value)
    assert "not in allowed models list" not in str(excinfo.value)


def test_describe_model_shows_a_refused_model_for_display():
    prov = OpenRouterProvider(api_key="test-key")
    described = prov.describe_model("openai/gpt-6-astra-pro")
    assert described is not None and described.requires_opt_in is True


# --- auto mode never offers a refused model ---------------------------------------------------------


def test_registry_auto_candidates_exclude_models_over_the_ceiling(monkeypatch):
    from providers.registry import ModelProviderRegistry

    prov = OpenRouterProvider(api_key="test-key")
    before = {m.lower() for m in ModelProviderRegistry._get_allowed_models_for_provider(prov, ProviderType.OPENROUTER)}
    assert "openai/gpt-6-astra" in before
    monkeypatch.setenv(cost_policy.CEILING_ENV, "20")
    after = {m.lower() for m in ModelProviderRegistry._get_allowed_models_for_provider(prov, ProviderType.OPENROUTER)}
    assert "openai/gpt-6-astra" not in after
    assert "anthropic/claude-haiku-5.5" in after


def test_ranked_summaries_exclude_refused_models(monkeypatch):
    from tools.chat import ChatTool

    tool = ChatTool()
    cheap = caps("cheap/model", intelligence_score=12, input_price_per_m=0.1, output_price_per_m=0.5)
    pricey = caps("pricey/model", intelligence_score=18, input_price_per_m=10.0, output_price_per_m=50.0)
    monkeypatch.setattr(
        tool, "_collect_ranked_capabilities", lambda: [(18, "pricey/model", pricey), (12, "cheap/model", cheap)]
    )
    monkeypatch.setenv(cost_policy.CEILING_ENV, "20")
    summaries, total, _ = tool._get_ranked_model_summaries()
    joined = " ".join(summaries)
    assert "pricey/model" not in joined
    assert "cheap/model" in joined
    assert total == 1
