"""Cost guard for expensive models.

Two settings protect a machine against an expensive model that a caller (often a coding agent) names
explicitly. ``auto_selectable: false`` keeps a model out of auto mode only; these settings also refuse
an explicit call.

- ``requires_opt_in`` (conf field): the model is refused unless ``ALLOW_OPT_IN_MODELS`` lists it. The
  OpenAI ``-pro`` presets and the OpenRouter Fusion routers set it, because one request can use many
  times the tokens of a plain call at the same per-token price.
- ``MAX_OUTPUT_PRICE_PER_M`` (environment): a model whose ``output_price_per_m`` (USD per million output
  tokens, conf field) is above this value is refused unless ``ALLOW_OPT_IN_MODELS`` lists it. A model
  with no price on record is not checked. Unset or empty means no ceiling.

``ALLOW_OPT_IN_MODELS`` is a comma-separated list of model names or aliases (case-insensitive), or ``all``.

Example::

    ALLOW_OPT_IN_MODELS=openai/gpt-6.1-sol-pro   # allow this one preset by name
    MAX_OUTPUT_PRICE_PER_M=20                    # refuse models above $20 per million output tokens
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils.env import get_env

if TYPE_CHECKING:
    from providers.shared import ModelCapabilities

logger = logging.getLogger(__name__)

ALLOW_ENV = "ALLOW_OPT_IN_MODELS"
CEILING_ENV = "MAX_OUTPUT_PRICE_PER_M"


def _allowed_names() -> set[str]:
    raw = get_env(ALLOW_ENV) or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def output_price_ceiling() -> float | None:
    """Return the configured ceiling in USD per million output tokens, or None when unset or invalid."""
    raw = (get_env(CEILING_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; no price ceiling is applied", CEILING_ENV, raw)
        return None
    if value <= 0:
        logger.warning("%s=%r must be greater than 0; no price ceiling is applied", CEILING_ENV, raw)
        return None
    return value


def price_value(value) -> float | None:
    """A stored price, or None when it is missing or not a real number (for example a test mock)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def needs_opt_in(capabilities) -> bool:
    return getattr(capabilities, "requires_opt_in", False) is True


def is_opted_in(capabilities: ModelCapabilities, *names: str | None) -> bool:
    """True when ALLOW_OPT_IN_MODELS is ``all`` or names the model by canonical name, alias, or a given name."""
    allowed = _allowed_names()
    if not allowed:
        return False
    if "all" in allowed:
        return True
    candidates = {n.lower() for n in names if isinstance(n, str) and n}
    model_name = getattr(capabilities, "model_name", None)
    if isinstance(model_name, str):
        candidates.add(model_name.lower())
    aliases = getattr(capabilities, "aliases", None)
    if isinstance(aliases, (list, tuple)):
        candidates.update(a.lower() for a in aliases if isinstance(a, str))
    return bool(candidates & allowed)


def refusal_reason(capabilities: ModelCapabilities, *names: str | None) -> str | None:
    """Return a user-facing reason when the cost guard refuses this model, or None when it is allowed."""
    if is_opted_in(capabilities, *names):
        return None
    name = getattr(capabilities, "model_name", None) or next((n for n in names if n), "?")
    if needs_opt_in(capabilities):
        return (
            f"Model '{name}' needs an explicit opt-in because one request can cost many times a normal call. "
            f"To allow it, add it to {ALLOW_ENV} (for example {ALLOW_ENV}={name}), or call a cheaper model."
        )
    ceiling = output_price_ceiling()
    price = price_value(getattr(capabilities, "output_price_per_m", None))
    if ceiling is not None and price is not None and price > ceiling:
        return (
            f"Model '{name}' costs ${price:g} per million output tokens, above {CEILING_ENV}=${ceiling:g}. "
            f"To allow it, add it to {ALLOW_ENV}, raise {CEILING_ENV}, or call a cheaper model."
        )
    return None


def is_refused(capabilities: ModelCapabilities, *names: str | None) -> bool:
    return refusal_reason(capabilities, *names) is not None
