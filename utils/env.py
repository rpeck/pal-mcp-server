"""Centralized environment variable access for PAL MCP Server."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    dotenv_values = None  # type: ignore[assignment]
    load_dotenv = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"

# A value with this prefix is a 1Password secret reference that get_env resolves with `op read`.
OP_REFERENCE_PREFIX = "op://"
_DEFAULT_OP_TIMEOUT_SECONDS = 60

_DOTENV_VALUES: dict[str, str | None] = {}
_FORCE_ENV_OVERRIDE = False
# Result of `op read` per reference; None records a failure so it is not tried again.
_OP_SECRETS: dict[str, str | None] = {}


def _read_dotenv_values() -> dict[str, str | None]:
    if dotenv_values is not None and _ENV_PATH.exists():
        loaded = dotenv_values(_ENV_PATH)
        return dict(loaded)
    return {}


def _compute_force_override(values: Mapping[str, str | None]) -> bool:
    raw = (values.get("PAL_MCP_FORCE_ENV_OVERRIDE") or "false").strip().lower()
    return raw == "true"


def reload_env(dotenv_mapping: Mapping[str, str | None] | None = None) -> None:
    """Reload .env values and recompute override semantics.

    Also forgets the 1Password secrets read so far, so references are read again on next use.

    Args:
        dotenv_mapping: Optional mapping used instead of reading the .env file.
            Intended for tests; when provided, load_dotenv is not invoked.
    """

    global _DOTENV_VALUES, _FORCE_ENV_OVERRIDE

    _OP_SECRETS.clear()

    if dotenv_mapping is not None:
        _DOTENV_VALUES = dict(dotenv_mapping)
        _FORCE_ENV_OVERRIDE = _compute_force_override(_DOTENV_VALUES)
        return

    _DOTENV_VALUES = _read_dotenv_values()
    _FORCE_ENV_OVERRIDE = _compute_force_override(_DOTENV_VALUES)

    if load_dotenv is not None and _ENV_PATH.exists():
        load_dotenv(dotenv_path=_ENV_PATH, override=_FORCE_ENV_OVERRIDE)


reload_env()


def env_override_enabled() -> bool:
    """Return True when PAL_MCP_FORCE_ENV_OVERRIDE is enabled via the .env file."""

    return _FORCE_ENV_OVERRIDE


def _raw_env(key: str) -> str | None:
    """Return the configured value of ``key`` without resolving 1Password references."""

    if env_override_enabled():
        return _DOTENV_VALUES.get(key)
    return os.getenv(key)


def get_env(key: str, default: str | None = None) -> str | None:
    """Retrieve environment variables respecting PAL_MCP_FORCE_ENV_OVERRIDE.

    A value that starts with ``op://`` is a 1Password secret reference. Return the secret that
    ``op read`` gives for it, or ``default`` when it cannot be read. Each reference is read once
    per process, on first use; a failure is also kept, so it logs one warning and is not retried.
    """

    value = _raw_env(key)
    if value is None:
        return default
    if not value.startswith(OP_REFERENCE_PREFIX):
        return value

    if value not in _OP_SECRETS:
        _OP_SECRETS[value] = _read_op_secret(key, value)
    secret = _OP_SECRETS[value]
    return secret if secret is not None else default


def _read_op_secret(key: str, reference: str) -> str | None:
    """Read a 1Password secret reference with ``op read``; on failure, log a warning and return None."""

    timeout = _op_timeout_seconds()
    command = ["op", "read", reference]
    account = _raw_env("PAL_OP_ACCOUNT")
    if account:
        command += ["--account", account]

    try:
        # PAL's stdin carries the MCP protocol, so op must not inherit it.
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        error = "the op command is not on PATH"
    except subprocess.TimeoutExpired:
        error = f"op read did not finish within {timeout}s (PAL_OP_TIMEOUT_SECONDS)"
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        error = str(exc)
    else:
        secret = result.stdout.removesuffix("\n")
        if result.returncode != 0:
            error = result.stderr.strip() or f"op read exited with status {result.returncode}"
        elif not secret:
            error = "op read returned an empty value"
        else:
            return secret

    logger.warning("Could not read %s from 1Password (%s): %s", key, reference, error)
    return None


def _op_timeout_seconds() -> int:
    raw = _raw_env("PAL_OP_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_OP_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning("PAL_OP_TIMEOUT_SECONDS=%r is not a whole number; using %ss", raw, _DEFAULT_OP_TIMEOUT_SECONDS)
        return _DEFAULT_OP_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_OP_TIMEOUT_SECONDS


def get_env_bool(key: str, default: bool = False) -> bool:
    """Boolean helper that respects override semantics."""

    raw_default = "true" if default else "false"
    raw_value = get_env(key, raw_default)
    return (raw_value or raw_default).strip().lower() == "true"


def get_all_env() -> dict[str, str | None]:
    """Expose the loaded .env mapping for diagnostics/logging; 1Password references stay unresolved."""

    return dict(_DOTENV_VALUES)


@contextmanager
def suppress_env_vars(*names: str):
    """Temporarily remove environment variables during the context.

    Args:
        names: Environment variable names to remove. Empty or falsy names are ignored.
    """

    removed: dict[str, str] = {}
    try:
        for name in names:
            if not name:
                continue
            if name in os.environ:
                removed[name] = os.environ[name]
                del os.environ[name]
        yield
    finally:
        for name, value in removed.items():
            os.environ[name] = value
