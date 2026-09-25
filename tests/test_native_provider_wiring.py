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
    ("qwen_models.json", "qwen3.8-flash", "qwen/qwen3.8-flash"),
    ("zai_models.json", "glm-5.3", "z-ai/glm-5.3"),
    ("zai_models.json", "glm-5.3-flash", "z-ai/glm-5.3-flash"),
    ("moonshot_models.json", "kimi-k3", "moonshotai/kimi-k3"),
    ("minimax_models.json", "MiniMax-M3", "minimax/minimax-m3"),
    ("anthropic_models.json", "claude-fable-5-1", "anthropic/claude-fable-5.1"),
    ("deepseek_models.json", "deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"),
    ("qwen_models.json", "qwen3.8-max-0902", "qwen/qwen3.8-max-0902"),
    ("openai_models.json", "gpt-6-astra", "openai/gpt-6-astra"),
    ("openai_models.json", "gpt-6-astra-pro", "openai/gpt-6-astra-pro"),
    ("anthropic_models.json", "claude-opus-5-5", "anthropic/claude-opus-5.5"),
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


def test_qwen38_flash_present_both_routes():
    # Production Qwen3.8 Flash: multimodal on both routes, and the bare "qwen-flash" alias now points
    # at it (newest production Flash), the same convention as glm-flash -> glm-5.3-flash.
    native = _models("qwen_models.json")
    assert "qwen3.8-flash" in native, "qwen3.8-flash missing from qwen_models.json"
    assert native["qwen3.8-flash"]["supports_images"] is True
    assert "qwen-flash" in native["qwen3.8-flash"]["aliases"]

    orm = {m["model_name"]: m for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]}
    assert "qwen/qwen3.8-flash" in orm, "qwen/qwen3.8-flash missing from openrouter_models.json"
    assert orm["qwen/qwen3.8-flash"]["supports_images"] is True
    assert "qwen-flash" in orm["qwen/qwen3.8-flash"]["aliases"]
    # The bare alias must not still hang off the older 3.6 mirror (duplicate-alias collision).
    assert "qwen-flash" not in orm["qwen/qwen3.6-flash"]["aliases"]


def test_fable_5_1_present_both_routes():
    # Claude Fable 5.1 (tops the current AA Index at 53): native claude-fable-5-1 + OpenRouter mirror,
    # score 20, and the bare "fable" alias now points at 5.1 (newest-wins).
    a = _models("anthropic_models.json")
    assert a["claude-fable-5-1"]["intelligence_score"] == 20
    assert "fable" in a["claude-fable-5-1"]["aliases"]
    assert "fable" not in a["claude-fable-5"]["aliases"], "bare 'fable' must move off Fable 5"

    orm = {m["model_name"]: m for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]}
    assert "anthropic/claude-fable-5.1" in orm
    assert orm["anthropic/claude-fable-5.1"]["intelligence_score"] == 20
    assert "fable" in orm["anthropic/claude-fable-5.1"]["aliases"]
    assert "fable" not in orm["anthropic/claude-fable-5"]["aliases"]


def test_qwen38_max_0902_native_present():
    # Production Qwen3.8-Max-0902 snapshot on DashScope (no OpenRouter mirror yet); the bare "qwen-max"
    # alias now points at it. Score held at the base Max tier (17) since no distinct AA Index exists.
    q = _models("qwen_models.json")
    assert "qwen3.8-max-0902" in q, "qwen3.8-max-0902 missing from qwen_models.json"
    assert q["qwen3.8-max-0902"]["intelligence_score"] == 17
    assert "qwen-max" in q["qwen3.8-max-0902"]["aliases"]
    assert "qwen-max" not in q["qwen3.8-max"]["aliases"], "bare 'qwen-max' must move off base Max"


def test_ox_alpha_livebench_score():
    orm = {m["model_name"]: m for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]}
    assert orm["stealth/ox-alpha"]["intelligence_score"] == 12  # LiveBench-derived, documented in docs/


def test_local_r1_distills_present_and_scored():
    custom = _models("custom_models.json")
    expected = {
        "deepseek-r1:1.5b": 6,
        "deepseek-r1:7b": 7,
        "deepseek-r1:8b": 8,
        "deepseek-r1:14b": 9,
        "deepseek-r1:32b": 10,
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


# --------------------------------------------------------------------------------------------------
# 2026-09 re-anchor: scores mapped to the current Artificial Analysis Intelligence Index (v4.1.1),
# plus the models added in that pass.
# --------------------------------------------------------------------------------------------------


def _orm():
    return {m["model_name"]: m for m in json.loads((CONF / "openrouter_models.json").read_text())["models"]}


def test_gpt6_astra_present_both_routes():
    # OpenAI GPT-6 Astra ties the top of the current AA Index at 53 (max effort) -> score 20.
    native = _models("openai_models.json")
    for name in ("gpt-6-astra", "gpt-6-astra-pro"):
        assert name in native, f"{name} missing from openai_models.json"
        assert native[name]["intelligence_score"] == 20
    assert "astra" in native["gpt-6-astra"]["aliases"]

    orm = _orm()
    for name in ("openai/gpt-6-astra", "openai/gpt-6-astra-pro"):
        assert name in orm, f"{name} missing from openrouter_models.json"
        assert orm[name]["intelligence_score"] == 20


def test_deepseek_v41_flash_supersedes_retired_models():
    # V4.1 Flash (AA 40) replaces V4 Flash and V4 Flash Vision Exp, which DeepSeek retired, so the
    # bare "deepseek" / "deepseek-flash" aliases move to it on both routes.
    ds = _models("deepseek_models.json")
    assert ds["deepseek-v4.1-flash"]["intelligence_score"] == 15
    assert ds["deepseek-v4.1-flash"]["supports_images"] is True
    assert "deepseek" in ds["deepseek-v4.1-flash"]["aliases"]
    assert "deepseek-flash" in ds["deepseek-v4.1-flash"]["aliases"]
    assert "deepseek" not in ds["deepseek-v4-pro"]["aliases"]
    assert "deepseek-flash" not in ds["deepseek-v4-flash"]["aliases"]
    # The retired entries must say so, so nobody treats them as current.
    assert "RETIRED" in ds["deepseek-v4-flash"]["description"]
    assert "RETIRED" in ds["deepseek-v4-flash-vision-exp"]["description"]
    assert "DEPRECATED" in ds["deepseek-v4-pro"]["description"]

    orm = _orm()
    assert orm["deepseek/deepseek-v4.1-flash"]["intelligence_score"] == 15
    assert "deepseek" in orm["deepseek/deepseek-v4.1-flash"]["aliases"]


def test_qwen38_max_0902_openrouter_mirror():
    # The 0902 snapshot reached OpenRouter, so the mirror exists and is score-pinned to the native entry.
    orm = _orm()
    assert "qwen/qwen3.8-max-0902" in orm
    assert orm["qwen/qwen3.8-max-0902"]["intelligence_score"] == 17
    assert "qwen-max" in orm["qwen/qwen3.8-max-0902"]["aliases"]
    assert "qwen-max" not in orm["qwen/qwen3.8-max"]["aliases"]


def test_reanchor_top_of_catalog():
    # The re-anchor must leave a single coherent top: Fable 5.1 and GPT-6 Astra at 20, Opus 5 at 19,
    # and no legacy model left above them.
    a = _models("anthropic_models.json")
    o = _models("openai_models.json")
    assert a["claude-fable-5-1"]["intelligence_score"] == 20
    assert o["gpt-6-astra"]["intelligence_score"] == 20
    assert a["claude-opus-5"]["intelligence_score"] == 19
    assert o["gpt-5.6-sol"]["intelligence_score"] == 18
    # No model anywhere may exceed the top of the scale.
    import glob

    for path in glob.glob(str(CONF / "*_models.json")):
        for m in json.loads(open(path).read()).get("models", []):
            assert 1 <= m["intelligence_score"] <= 20, f"{m['model_name']} out of range in {path}"


def test_gemini_38_flash_and_family_refresh():
    # 2026-09-24 refresh. Gemini 3.8 Flash (AA 41) is the newest Flash, so it takes both the
    # "gemini-flash-latest" alias and the gated dynamic "flash" alias from 3.7.
    g = _models("gemini_models.json")
    assert g["gemini-3.8-flash"]["intelligence_score"] == 16
    assert "gemini-flash-latest" in g["gemini-3.8-flash"]["aliases"]
    assert "flash" in g["gemini-3.8-flash"]["dynamic_aliases"]
    assert "gemini-flash-latest" not in g["gemini-3.7-flash"]["aliases"]
    assert "dynamic_aliases" not in g["gemini-3.7-flash"]

    # Newest entry per open-weight family, each scored from the current AA Index.
    orm = _orm()
    expected = {
        "google/gemini-3.8-flash": 16,
        "meta/muse-spark-1.3": 18,
        "xiaomi/mimo-v2.6-pro": 18,
        "qwen/qwen3.8-27b": 13,
        "moonshotai/kimi-k2.7-code": 10,
        "nvidia/nemotron-3-ultra-550b-a55b": 9,
    }
    for name, score in expected.items():
        assert name in orm, f"{name} missing from openrouter_models.json"
        assert orm[name]["intelligence_score"] == score, f"{name}: expected {score}"

    # Bare family aliases follow newest-wins off the superseded entries.
    assert "muse" in orm["meta/muse-spark-1.3"]["aliases"]
    assert "muse" not in orm["meta/muse-spark-1.2"]["aliases"]
    assert "mimo" in orm["xiaomi/mimo-v2.6-pro"]["aliases"]
    assert "mimo" not in orm["xiaomi/mimo-v2.5-pro"]["aliases"]


def test_mimo_v26_series_complete():
    # Xiaomi announced the MiMo-V2.6 series (2026-09-22) as three models, not one. All three are
    # MIT-weight, 1M context and omnimodal. Pro is AA-scored at 46; the other two are provisional.
    orm = _orm()
    expected = {
        "xiaomi/mimo-v2.6-pro": 18,  # AA Index 46, measured
        "xiaomi/mimo-v2.6-flash": 17,  # provisional: within 4 points of Pro, one tier below
        "xiaomi/mimo-v2.6-pro-ultraspeed": 18,  # provisional: vendor-claimed parity with Pro
    }
    for name, score in expected.items():
        assert name in orm, f"{name} missing from openrouter_models.json"
        assert orm[name]["intelligence_score"] == score, f"{name}: expected {score}"
        assert orm[name]["supports_images"] is True, f"{name}: omnimodal, must accept images"
    # The two unmeasured entries must say so, so nobody mistakes them for AA-anchored scores.
    for name in ("xiaomi/mimo-v2.6-flash", "xiaomi/mimo-v2.6-pro-ultraspeed"):
        assert "PROVISIONAL" in orm[name]["description"], f"{name}: must be tagged PROVISIONAL"
    # Bare family aliases stay on the flagship.
    assert "mimo" in orm["xiaomi/mimo-v2.6-pro"]["aliases"]


def test_claude_opus_55_both_routes():
    # Claude Opus 5.5 (2026-09-22) scores 58 at max effort, above Fable 5.1 and GPT-6 Astra (53).
    # The 1-20 scale tops out at 20, so all three share the top score.
    a = _models("anthropic_models.json")
    assert a["claude-opus-5-5"]["intelligence_score"] == 20
    assert "opus" in a["claude-opus-5-5"]["aliases"]
    assert "opus" not in a["claude-opus-5"]["aliases"], "bare 'opus' must move off Opus 5"

    orm = _orm()
    assert orm["anthropic/claude-opus-5.5"]["intelligence_score"] == 20
    assert "opus" in orm["anthropic/claude-opus-5.5"]["aliases"]
    # On the OpenRouter route the bare alias used to hang off the legacy Opus 4.5.
    assert "opus" not in orm["anthropic/claude-opus-4.5"]["aliases"]
