"""Registry loader for models reachable with a ChatGPT plan login (via the Codex CLI)."""

from __future__ import annotations

from ..shared import ModelCapabilities, ProviderType
from ..shared.effort import EFFORT_ORDER
from .base import CapabilityModelRegistry

# Per-model call limits. They are not ModelCapabilities fields because only this provider uses them.
TIMEOUT_KEY = "timeout_seconds"
TIMEOUT_BY_EFFORT_KEY = "timeout_seconds_by_effort"


class ChatGPTModelRegistry(CapabilityModelRegistry):
    """Capability registry backed by ``conf/chatgpt_models.json``."""

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(
            env_var_name="CHATGPT_MODELS_CONFIG_PATH",
            default_filename="chatgpt_models.json",
            provider=ProviderType.CHATGPT,
            friendly_prefix="ChatGPT ({model})",
            config_path=config_path,
        )

    def _extra_keys(self) -> set[str]:
        return {TIMEOUT_KEY, TIMEOUT_BY_EFFORT_KEY}

    def _finalise_entry(self, entry: dict) -> tuple[ModelCapabilities, dict]:
        capability, _ = super()._finalise_entry(entry)
        name = capability.model_name
        extras: dict = {}

        if entry.get(TIMEOUT_KEY) is not None:
            extras[TIMEOUT_KEY] = _positive_seconds(entry[TIMEOUT_KEY], f"{name}: {TIMEOUT_KEY}")

        by_effort = entry.get(TIMEOUT_BY_EFFORT_KEY)
        if by_effort is not None:
            if not isinstance(by_effort, dict):
                raise ValueError(f"{name}: {TIMEOUT_BY_EFFORT_KEY} must map effort levels to seconds")
            known = capability.effort_levels or list(EFFORT_ORDER)
            for effort, seconds in by_effort.items():
                if effort not in known:
                    raise ValueError(
                        f"{name}: {TIMEOUT_BY_EFFORT_KEY} names effort '{effort}', which is not one of {known}"
                    )
                _positive_seconds(seconds, f"{name}: {TIMEOUT_BY_EFFORT_KEY}.{effort}")
            if by_effort:
                extras[TIMEOUT_BY_EFFORT_KEY] = dict(by_effort)

        return capability, extras


def _positive_seconds(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive whole number of seconds, not {value!r}")
    return value
