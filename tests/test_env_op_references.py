"""Tests for 1Password (op://) secret references in utils.env.get_env."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import utils.env as env_config
from clink.agents.base import BaseCLIAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole

REFERENCE = "op://vault/item/credential"
SECRET = "s3cret-value"


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["op"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def op_run(monkeypatch):
    """Replace subprocess.run so no test calls the real op CLI."""

    run = MagicMock(return_value=_completed(stdout=f"{SECRET}\n"))
    monkeypatch.setattr(subprocess, "run", run)
    for name in ("PAL_OP_ACCOUNT", "PAL_OP_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    return run


def test_reference_resolves_once_and_is_cached(monkeypatch, op_run, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", REFERENCE)

    with caplog.at_level(logging.DEBUG):
        assert env_config.get_env("GEMINI_API_KEY") == SECRET
        assert env_config.get_env("GEMINI_API_KEY") == SECRET

    op_run.assert_called_once()
    args, kwargs = op_run.call_args
    assert args[0] == ["op", "read", REFERENCE]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 60
    assert not kwargs.get("shell")
    assert SECRET not in caplog.text


def test_only_one_trailing_newline_is_removed(monkeypatch, op_run):
    op_run.return_value = _completed(stdout="line one\nline two\n\n")
    monkeypatch.setenv("PAL_TEST_MULTILINE", REFERENCE)

    assert env_config.get_env("PAL_TEST_MULTILINE") == "line one\nline two\n"


def test_account_and_timeout_settings_are_passed_to_op(monkeypatch, op_run):
    monkeypatch.setenv("GEMINI_API_KEY", REFERENCE)
    monkeypatch.setenv("PAL_OP_ACCOUNT", "my-team.1password.com")
    monkeypatch.setenv("PAL_OP_TIMEOUT_SECONDS", "5")

    assert env_config.get_env("GEMINI_API_KEY") == SECRET

    args, kwargs = op_run.call_args
    assert args[0] == ["op", "read", REFERENCE, "--account", "my-team.1password.com"]
    assert kwargs["timeout"] == 5


@pytest.mark.parametrize(
    ("outcome", "error_text"),
    [
        (FileNotFoundError("op"), "not on PATH"),
        (subprocess.TimeoutExpired(cmd="op", timeout=60), "did not finish within 60s"),
        (_completed(returncode=1, stderr="[ERROR] account is not signed in\n"), "account is not signed in"),
        (_completed(stdout="\n"), "empty value"),
    ],
    ids=["not-installed", "timeout", "non-zero-exit", "empty-output"],
)
def test_failure_returns_default_warns_once_and_is_not_retried(monkeypatch, op_run, caplog, outcome, error_text):
    if isinstance(outcome, Exception):
        op_run.side_effect = outcome
    else:
        op_run.return_value = outcome
    monkeypatch.setenv("GEMINI_API_KEY", REFERENCE)

    with caplog.at_level(logging.WARNING, logger="utils.env"):
        assert env_config.get_env("GEMINI_API_KEY", "fallback") == "fallback"
        assert env_config.get_env("GEMINI_API_KEY") is None

    op_run.assert_called_once()
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "GEMINI_API_KEY" in message
    assert error_text in message


def test_plain_value_is_returned_unchanged(monkeypatch, op_run):
    monkeypatch.setenv("GEMINI_API_KEY", "plain-key")

    assert env_config.get_env("GEMINI_API_KEY") == "plain-key"
    assert env_config.get_env("PAL_TEST_UNSET_VARIABLE", "fallback") == "fallback"
    op_run.assert_not_called()


def test_reference_in_dotenv_file_resolves(monkeypatch, tmp_path: Path, op_run):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(f"PAL_TEST_DOTENV_KEY={REFERENCE}\n")
    # Set, then delete, so monkeypatch removes the value that load_dotenv puts in os.environ.
    monkeypatch.setenv("PAL_TEST_DOTENV_KEY", "placeholder")
    monkeypatch.delenv("PAL_TEST_DOTENV_KEY")
    original_env_path = env_config._ENV_PATH
    env_config._ENV_PATH = dotenv_path

    try:
        env_config.reload_env()
        assert env_config.get_env("PAL_TEST_DOTENV_KEY") == SECRET
    finally:
        env_config._ENV_PATH = original_env_path
        env_config.reload_env({"PAL_MCP_FORCE_ENV_OVERRIDE": "false"})

    op_run.assert_called_once()


def test_force_override_reads_references_from_dotenv_only(monkeypatch, op_run):
    monkeypatch.setenv("GEMINI_API_KEY", "shell-value")
    monkeypatch.setenv("OPENAI_API_KEY", "shell-only")
    env_config.reload_env(
        {"PAL_MCP_FORCE_ENV_OVERRIDE": "true", "GEMINI_API_KEY": REFERENCE, "XAI_API_KEY": "dotenv-plain"}
    )

    assert env_config.get_env("GEMINI_API_KEY") == SECRET
    assert env_config.get_env("XAI_API_KEY") == "dotenv-plain"
    assert env_config.get_env("OPENAI_API_KEY", "fallback") == "fallback"
    op_run.assert_called_once()


def test_without_override_os_environ_wins_over_dotenv(monkeypatch, op_run):
    monkeypatch.setenv("GEMINI_API_KEY", "shell-value")
    env_config.reload_env({"PAL_MCP_FORCE_ENV_OVERRIDE": "false", "GEMINI_API_KEY": REFERENCE})

    assert env_config.get_env("GEMINI_API_KEY") == "shell-value"
    op_run.assert_not_called()


def test_get_all_env_keeps_the_reference(op_run):
    env_config.reload_env({"PAL_MCP_FORCE_ENV_OVERRIDE": "true", "GEMINI_API_KEY": REFERENCE})

    assert env_config.get_env("GEMINI_API_KEY") == SECRET
    all_env = env_config.get_all_env()
    assert all_env["GEMINI_API_KEY"] == REFERENCE
    assert SECRET not in all_env.values()


def test_clink_child_environment_gets_secrets_not_references(monkeypatch, op_run):
    monkeypatch.setenv("GEMINI_API_KEY", REFERENCE)
    monkeypatch.setenv("PAL_TEST_BROKEN_REFERENCE", "op://vault/missing/credential")
    monkeypatch.setenv("PAL_TEST_PLAIN_VALUE", "plain")
    op_run.side_effect = lambda command, **_kwargs: (
        _completed(stdout=f"{SECRET}\n") if command[2] == REFERENCE else _completed(returncode=1, stderr="not found")
    )
    role = ResolvedCLIRole(name="default", prompt_path=Path("systemprompts/clink/default.txt").resolve(), role_args=[])
    client = ResolvedCLIClient(
        name="gemini",
        executable=["gemini"],
        working_dir=None,
        timeout_seconds=30,
        parser="gemini_json",
        roles={"default": role},
    )

    env = BaseCLIAgent(client)._build_environment()

    assert env["GEMINI_API_KEY"] == SECRET
    assert "PAL_TEST_BROKEN_REFERENCE" not in env
    assert env["PAL_TEST_PLAIN_VALUE"] == "plain"
