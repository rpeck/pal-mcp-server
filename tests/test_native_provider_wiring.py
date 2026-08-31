"""Cross-cutting wiring + coverage invariants for the native open-model providers.

These lock the gaps an adhd audit surfaced: four-registry bijection, natives-before-OpenRouter
ordering, cross-route score pinning, per-model conf well-formedness, vision flags, key-gating, and
the local R1 distills. They are route-independent (load conf JSON directly) so they never make a
network call.
"""

import json
from pathlib import Path

import pytest

from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from utils.model_restrictions import ModelRestrictionService

CONF = Path(__file__).resolve().parents[1] / "conf"

# The 8 native OpenAI-compatible providers added in this batch: (ProviderType, env var, conf filename).
NATIVE_VENDORS = [
    (ProviderType.DEEPSEEK, "DEEPSEEK_API_KEY", "deepseek_models.json"),
    (ProviderType.QWEN, "DASHSCOPE_API_KEY", "qwen_models.json"),
    (ProviderType.ZAI, "ZAI_API_KEY", "zai_models.json"),
    (ProviderType.MOONSHOT, "MOONSHOT_API_KEY", "moonshot_models.json"),
    (ProviderType.MINIMAX, "MINIMAX_API_KEY", "minimax_models.json"),
    (ProviderType.MISTRAL, "MISTRAL_API_KEY", "mistral_models.json"),
    (ProviderType.NVIDIA, "NVIDIA_API_KEY", "nvidia_models.json"),
    (ProviderType.PERPLEXITY, "PERPLEXITY_API_KEY", "perplexity_models.json"),
]

# Native model -> OpenRouter mirror model whose intelligence_score must stay identical (score pinning).
SCORE_PINS = [
    ("deepseek_models.json", "deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
    ("deepseek_models.json", "deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
    ("deepseek_models.json", "deepseek-reasoner", "deepseek/deepseek-r1-0528"),
    ("qwen_models.json", "qwen3.8-max", "qwen/qwen3.8-max"),
    ("zai_models.json", "glm-5.3", "z-ai/glm-5.3"),
    ("zai_models.json", "glm-5.3-flash", "z-ai/glm-5.3-flash"),
    ("moonshot_models.json", "kimi-k3", "moonshotai/kimi-k3"),
    ("minimax_models.json", "MiniMax-M3", "minimax/minimax-m3"),
]


def _models(filename):
    return {m["model_name"]: m for m in json.loads((CONF / filename).read_text())["models"]}


def _or_scores():
    return {
        m["model_name"]: m.get("intelligence_score")
        for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]
    }


@pytest.mark.parametrize("ptype,env_var,conf", NATIVE_VENDORS)
def test_providertype_wired_in_all_registries(ptype, env_var, conf, monkeypatch):
    # Bijection: each new ProviderType must appear in priority order, both restriction maps, and the
    # registry key map (a member wired into one place but missing from another breaks resolution).
    assert ptype in ModelProviderRegistry.PROVIDER_PRIORITY_ORDER, f"{ptype} missing from PROVIDER_PRIORITY_ORDER"
    assert ptype in ModelRestrictionService.ENV_VARS, f"{ptype} missing from ENV_VARS"
    assert ptype in ModelRestrictionService.DISALLOWED_ENV_VARS, f"{ptype} missing from DISALLOWED_ENV_VARS"
    monkeypatch.setenv(env_var, "sentinel-key")
    assert ModelProviderRegistry._get_api_key_for_provider(ptype) == "sentinel-key", f"{ptype} key map wrong/absent"


@pytest.mark.parametrize("ptype,env_var,conf", NATIVE_VENDORS)
def test_native_ranks_before_openrouter(ptype, env_var, conf):
    order = ModelProviderRegistry.PROVIDER_PRIORITY_ORDER
    assert order.index(ptype) < order.index(ProviderType.OPENROUTER), f"{ptype} must rank before OpenRouter"


def test_env_var_names_unique():
    names = [env for _, env, _ in NATIVE_VENDORS]
    assert len(names) == len(set(names)), "duplicate/typo-shadowed API-key env var names"


@pytest.mark.parametrize("conf,native_model,or_model", SCORE_PINS)
def test_cross_route_score_pinning(conf, native_model, or_model):
    native = _models(conf)
    assert native_model in native, f"{native_model} missing from {conf}"
    or_scores = _or_scores()
    assert or_model in or_scores, f"{or_model} missing from openrouter_models.json"
    assert (
        native[native_model]["intelligence_score"] == or_scores[or_model]
    ), f"score drift: {native_model} != {or_model}"


@pytest.mark.parametrize("ptype,env_var,conf", NATIVE_VENDORS)
def test_conf_well_formed(ptype, env_var, conf):
    models = _models(conf)
    assert models, f"{conf} has no models"
    seen_aliases = {}
    for name, m in models.items():
        assert m.get("context_window", 0) > 0, f"{name}: context_window must be > 0"
        assert m.get("max_output_tokens", 0) > 0, f"{name}: max_output_tokens must be > 0"
        assert 1 <= m.get("intelligence_score", 0) <= 20, f"{name}: score out of range"
        assert m.get("aliases"), f"{name}: must declare at least one alias"
        assert isinstance(m.get("supports_images"), bool), f"{name}: supports_images must be an explicit bool"
        for a in m["aliases"]:
            assert (
                a.lower() not in seen_aliases
            ), f"{conf}: duplicate alias '{a}' ({name} vs {seen_aliases.get(a.lower())})"
            seen_aliases[a.lower()] = name


def test_vision_flags_explicit():
    ds = _models("deepseek_models.json")
    assert ds["deepseek-v4-flash-vision-exp"]["supports_images"] is True
    assert ds["deepseek-v4-pro"]["supports_images"] is False  # non-vision must NOT claim images
    orm = {m["model_name"]: m for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]}
    assert orm["deepseek/deepseek-v4-flash-vision-exp"]["supports_images"] is True
    assert orm["stealth/ox-alpha"]["supports_images"] is True


def test_ox_alpha_livebench_score():
    orm = {m["model_name"]: m for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]}
    assert orm["stealth/ox-alpha"]["intelligence_score"] == 15  # LiveBench-derived, documented in docs/


def test_local_r1_distills_present_and_scored():
    custom = _models("custom_models.json")
    expected = {
        "deepseek-r1:1.5b": 8,
        "deepseek-r1:7b": 9,
        "deepseek-r1:8b": 10,
        "deepseek-r1:14b": 11,
        "deepseek-r1:32b": 12,
    }
    for name, score in expected.items():
        assert name in custom, f"local distill {name} missing"
        assert custom[name]["intelligence_score"] == score
        assert custom[name]["supports_extended_thinking"] is True
    assert "deepseek-r1:70b" not in custom, "70B distill must be excluded (OOMs a single local box)"


@pytest.mark.parametrize("ptype,env_var,conf", NATIVE_VENDORS)
def test_dormant_without_key(ptype, env_var, conf, monkeypatch):
    # Key-gating: with the env var unset, the registry must not hand back an initialized provider.
    monkeypatch.delenv(env_var, raising=False)
    ModelProviderRegistry.reset_for_testing()
    from importlib import import_module

    module_name = {
        ProviderType.DEEPSEEK: "providers.deepseek",
        ProviderType.QWEN: "providers.qwen",
        ProviderType.ZAI: "providers.zai",
        ProviderType.MOONSHOT: "providers.moonshot",
        ProviderType.MINIMAX: "providers.minimax",
        ProviderType.MISTRAL: "providers.mistral",
        ProviderType.NVIDIA: "providers.nvidia",
        ProviderType.PERPLEXITY: "providers.perplexity",
    }[ptype]
    cls = next(v for k, v in vars(import_module(module_name)).items() if k.endswith("ModelProvider"))
    ModelProviderRegistry.register_provider(ptype, cls)
    assert ModelProviderRegistry.get_provider(ptype) is None, f"{ptype} initialized without a key"
    ModelProviderRegistry.reset_for_testing()


def test_every_native_provider_has_a_deferred_live_test():
    # "Deferred but present": each native provider must carry a (skipif-no-key) live test, so a missing
    # key is an explicit skip, never a silently-absent provider.
    from tests.test_native_provider_live import LIVE_VENDORS

    covered = {ptype for ptype, *_ in LIVE_VENDORS}
    expected = {ptype for ptype, *_ in NATIVE_VENDORS}
    assert covered == expected, f"native providers without a deferred live test: {expected - covered}"
