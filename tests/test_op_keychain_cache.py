"""Tests for the optional keychain cache of 1Password secrets (PAL_OP_CACHE=keychain): utils.env and the module.

No test touches a real keychain: a fake replaces the Security framework binding. CI runs on Linux.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import utils.env as env_config
from utils import op_keychain_cache as cache

ACCOUNT = "my-team.1password.com"
REFERENCE = "op://vault/item/credential"
OTHER_REFERENCE = "op://vault/other/credential"
SECRET = "cached-s3cret-value"
OP_SECRET = "op-s3cret-value"
CDHASH = "c0ffee" * 6 + "c0ff"
PRIVATE = cache.Identity(CDHASH, cache.PRIVATE_IDENTIFIER_PREFIX + "abc123", adhoc=True, team_id=None)
FAR_FUTURE = 4_000_000_000.0


class FakeKeychain:
    """Stands in for utils.op_keychain_cache._MacKeychain."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.identity = PRIVATE
        self.states: dict[Path, str] = {}
        self.items: dict[tuple[Path, str], bytes] = {}
        self.calls: list[str] = []
        self.error: Exception | None = None

    def own_identity(self):
        self.calls.append("own_identity")
        if self.error:
            raise self.error
        return self.identity

    def state(self, path):
        self.calls.append("state")
        return self.states.get(path, cache.ABSENT)

    def find(self, path, account):
        self.calls.append("find")
        return self.items.get((path, account))

    def store(self, reference, value, expires_at=FAR_FUTURE, state=cache.UNLOCKED, account=ACCOUNT, write_state=True):
        path = cache.keychain_path(CDHASH)
        self.states[path] = state
        self.items[(path, cache.item_account(account, reference))] = cache.encode_item(
            account, reference, value, expires_at
        )
        if write_state:
            cache.write_state(cache.CacheState(CDHASH, 0.0, FAR_FUTURE, ((account, reference),)))
        return path


@pytest.fixture()
def keychain(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "KEYCHAIN_DIR", tmp_path / "Keychains")
    monkeypatch.setattr(cache, "STATE_DIR", tmp_path / "state")
    fake = FakeKeychain(tmp_path)
    monkeypatch.setattr(cache, "backend", lambda: fake)
    return fake


@pytest.fixture()
def op_run(monkeypatch):
    """Replace subprocess.run so no test calls the real op CLI."""

    run = MagicMock(
        return_value=subprocess.CompletedProcess(args=["op"], returncode=0, stdout=f"{OP_SECRET}\n", stderr="")
    )
    monkeypatch.setattr(subprocess, "run", run)
    for name in ("PAL_OP_ACCOUNT", "PAL_OP_TIMEOUT_SECONDS", "PAL_OP_CACHE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", REFERENCE)
    return run


@pytest.fixture()
def cache_on(monkeypatch):
    monkeypatch.setenv("PAL_OP_CACHE", "keychain")
    monkeypatch.setenv("PAL_OP_ACCOUNT", ACCOUNT)
    monkeypatch.setattr(sys, "platform", "darwin")


# --- Off by default -------------------------------------------------------------------------------------------


def test_cache_is_off_by_default_and_never_touches_a_keychain(monkeypatch, op_run, caplog):
    # If this test fails, the default changed. The cache must stay opt-in (PLANS: "off by default").
    def no_keychain(*args, **kwargs):
        raise AssertionError("PAL must not touch a keychain when PAL_OP_CACHE is not set")

    monkeypatch.setattr(cache, "backend", no_keychain)
    monkeypatch.setattr(cache, "lookup", no_keychain)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PAL_OP_ACCOUNT", ACCOUNT)

    with caplog.at_level(logging.DEBUG):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    op_run.assert_called_once()
    assert "keychain" not in caplog.text.lower()


def test_the_default_path_does_not_import_the_cache_module(tmp_path):
    code = (
        "import sys, utils.env as e; "
        "assert e.get_env('PAL_TEST_PLAIN') == 'plain'; "
        "print('utils.op_keychain_cache' in sys.modules)"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("PAL_OP_")}
    env.update(PAL_TEST_PLAIN="plain", PAL_MCP_FORCE_ENV_OVERRIDE="false")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("value", ["off", "OFF", " off ", ""])
def test_off_values_keep_todays_behavior(monkeypatch, op_run, keychain, value, caplog):
    monkeypatch.setenv("PAL_OP_CACHE", value)
    monkeypatch.setenv("PAL_OP_ACCOUNT", ACCOUNT)
    monkeypatch.setattr(sys, "platform", "darwin")

    with caplog.at_level(logging.DEBUG):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    assert keychain.calls == []
    assert caplog.text == ""


# --- Hits and misses ------------------------------------------------------------------------------------------


def test_cache_hit_returns_the_value_without_op(op_run, keychain, cache_on, caplog):
    keychain.store(REFERENCE, SECRET)

    with caplog.at_level(logging.DEBUG):
        assert env_config.get_env("GEMINI_API_KEY") == SECRET
        assert env_config.get_env("GEMINI_API_KEY") == SECRET

    op_run.assert_not_called()
    assert keychain.calls.count("find") == 1, "each reference is looked up once per process"
    assert "Read GEMINI_API_KEY from the keychain cache" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    ("setup", "result"),
    [
        (lambda fake: fake.store(REFERENCE, SECRET, state=cache.LOCKED), "locked"),
        (lambda fake: fake.store(REFERENCE, SECRET, expires_at=1.0), "expired"),
        (lambda fake: fake.store(OTHER_REFERENCE, SECRET), "missing"),
        (lambda fake: fake.store(REFERENCE, SECRET, write_state=False), "missing"),
        (lambda fake: None, "missing"),
    ],
    ids=["locked-keychain", "expired-item", "missing-item", "no-state-file", "nothing"],
)
def test_every_cache_miss_falls_back_to_op_read(op_run, keychain, cache_on, caplog, setup, result):
    setup(keychain)

    with caplog.at_level(logging.INFO):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    op_run.assert_called_once()
    assert f"The keychain cache is {result} for GEMINI_API_KEY; reading from 1Password" in caplog.text
    assert SECRET not in caplog.text
    if result == "locked":
        assert "find" not in keychain.calls, "a read of a locked keychain shows a dialog"


def test_the_state_file_is_checked_before_the_keychain(keychain):
    keychain.store(REFERENCE, SECRET)
    cache.write_state(cache.CacheState(CDHASH, 0.0, 1_000.0, ((ACCOUNT, REFERENCE),)))

    # The margin is 5 minutes, or a tenth of the TTL if that is shorter: 100 s here.
    assert cache.lookup(ACCOUNT, REFERENCE, now=899.0) == (SECRET, cache.HIT)
    keychain.calls.clear()
    assert cache.lookup(ACCOUNT, REFERENCE, now=900.0) == (None, cache.EXPIRED)
    assert "state" not in keychain.calls and "find" not in keychain.calls


def test_the_cache_is_bound_to_the_account(keychain):
    keychain.store(REFERENCE, SECRET)

    assert cache.lookup("another-account", REFERENCE, now=1.0) == (None, cache.MISSING)
    assert cache.item_account(ACCOUNT, REFERENCE) != cache.item_account("another-account", REFERENCE)
    # The parts carry their lengths, so moving text between them gives another name.
    assert cache.item_account("ab", "c") != cache.item_account("a", "bc")


def test_an_item_for_another_reference_is_rejected(op_run, keychain, cache_on, caplog):
    path = keychain.store(REFERENCE, SECRET)
    # Put an item for another reference under the name of REFERENCE.
    keychain.items[(path, cache.item_account(ACCOUNT, REFERENCE))] = cache.encode_item(
        ACCOUNT, OTHER_REFERENCE, SECRET, FAR_FUTURE
    )

    with caplog.at_level(logging.INFO):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    assert "belongs to another account or reference" in caplog.text
    assert SECRET not in caplog.text


def test_a_corrupt_item_falls_back_without_its_data_in_the_log(op_run, keychain, cache_on, caplog):
    path = keychain.store(REFERENCE, SECRET)
    keychain.items[(path, cache.item_account(ACCOUNT, REFERENCE))] = f'{{"value": "{SECRET}"'.encode()

    with caplog.at_level(logging.INFO):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    assert "not in the expected format" in caplog.text
    assert SECRET not in caplog.text


# --- Who can use the cache --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identity", "text"),
    [
        (cache.Identity(CDHASH, "-", adhoc=True, team_id=None), "this Python is not the private copy"),
        (cache.Identity(CDHASH, "com.apple.python3", adhoc=False, team_id="59GAB85EFG"), "team signature"),
        (cache.Identity(CDHASH, cache.PRIVATE_IDENTIFIER_PREFIX + "x", adhoc=False, team_id=None), "not ad-hoc"),
    ],
    ids=["shared-interpreter", "apple-signed", "not-adhoc"],
)
def test_only_the_private_python_copy_can_use_the_cache(
    monkeypatch, op_run, keychain, cache_on, caplog, identity, text
):
    keychain.store(REFERENCE, SECRET)
    keychain.identity = identity
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_REFERENCE)

    with caplog.at_level(logging.WARNING):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET
        assert env_config.get_env("OPENAI_API_KEY") == OP_SECRET

    assert len(caplog.records) == 1 and text in caplog.records[0].getMessage()
    assert "find" not in keychain.calls


def test_backend_error_falls_back_with_one_warning(monkeypatch, op_run, keychain, cache_on, caplog):
    keychain.error = cache.KeychainCacheError("SecCodeCopySelf failed with status -1")
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_REFERENCE)

    with caplog.at_level(logging.INFO):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET
        assert env_config.get_env("OPENAI_API_KEY") == OP_SECRET

    assert op_run.call_count == 2
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Cannot read the keychain cache" in warnings[0].getMessage()
    assert keychain.calls == ["own_identity"], "after one error, the cache stays off for the process"


@pytest.mark.parametrize(
    ("platform", "value", "account", "warning"),
    [
        ("darwin", "redis", ACCOUNT, "PAL_OP_CACHE='redis' is not 'off' or 'keychain'; the cache stays off"),
        ("linux", "keychain", ACCOUNT, "PAL_OP_CACHE=keychain needs macOS; the cache stays off"),
    ],
    ids=["unknown-value", "not-macos"],
)
def test_unusable_setting_warns_once_and_acts_as_off(
    monkeypatch, op_run, keychain, caplog, platform, value, account, warning
):
    monkeypatch.setenv("PAL_OP_CACHE", value)
    if account:
        monkeypatch.setenv("PAL_OP_ACCOUNT", account)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_REFERENCE)

    with caplog.at_level(logging.WARNING):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET
        assert env_config.get_env("OPENAI_API_KEY") == OP_SECRET

    assert [record.getMessage() for record in caplog.records] == [warning]
    assert keychain.calls == []


def test_a_variable_without_an_account_is_not_cached(monkeypatch, op_run, keychain, cache_on, caplog):
    monkeypatch.delenv("PAL_OP_ACCOUNT")
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_REFERENCE)
    monkeypatch.setenv("PAL_OP_ACCOUNT_OPENAI_API_KEY", ACCOUNT)
    keychain.store(OTHER_REFERENCE, SECRET)

    with caplog.at_level(logging.WARNING):
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET
        assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET
        assert env_config.get_env("OPENAI_API_KEY") == SECRET

    assert [record.getMessage() for record in caplog.records] == [
        "The keychain cache needs PAL_OP_ACCOUNT or PAL_OP_ACCOUNT_GEMINI_API_KEY; reading GEMINI_API_KEY from 1Password"
    ]
    op_run.assert_called_once()


def test_each_variable_can_have_its_own_account(monkeypatch, op_run, keychain, cache_on):
    monkeypatch.setenv("OPENAI_API_KEY", REFERENCE)
    monkeypatch.setenv("PAL_OP_ACCOUNT_OPENAI_API_KEY", "client.1password.com")
    keychain.store(REFERENCE, SECRET, account="client.1password.com")

    # The same reference under two accounts: two different secrets.
    assert env_config.get_env("OPENAI_API_KEY") == SECRET
    assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    op_run.assert_called_once()
    assert op_run.call_args.args[0] == ["op", "read", REFERENCE, "--account", ACCOUNT]


def test_op_read_uses_the_account_of_the_variable(monkeypatch, op_run):
    monkeypatch.setenv("PAL_OP_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("OPENAI_API_KEY", REFERENCE)
    monkeypatch.setenv("PAL_OP_ACCOUNT_OPENAI_API_KEY", "client.1password.com")

    assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET
    assert env_config.get_env("OPENAI_API_KEY") == OP_SECRET

    commands = [call.args[0] for call in op_run.call_args_list]
    assert commands == [
        ["op", "read", REFERENCE, "--account", ACCOUNT],
        ["op", "read", REFERENCE, "--account", "client.1password.com"],
    ]


def test_the_cache_setting_is_never_resolved_as_a_reference(monkeypatch, op_run, keychain):
    monkeypatch.setenv("PAL_OP_CACHE", "op://vault/item/cache-mode")
    monkeypatch.setattr(sys, "platform", "darwin")

    assert env_config.get_env("GEMINI_API_KEY") == OP_SECRET

    # One op call, for the API key; none for the setting.
    op_run.assert_called_once()
    assert keychain.calls == []


# --- Files ------------------------------------------------------------------------------------------------------


def test_state_file_round_trip_and_rejects(keychain):
    state = cache.CacheState(CDHASH, 1.0, 2.0, ((ACCOUNT, REFERENCE),))
    cache.write_state(state)

    assert cache.read_state(CDHASH) == state
    assert list(cache.STATE_DIR.iterdir()) == [cache.state_path(CDHASH)], "no temporary file stays"
    cache.state_path("other").write_text(cache.state_path(CDHASH).read_text())
    assert cache.read_state("other") is None, "a state file for another cdhash is not valid"
    cache.state_path(CDHASH).write_text("{not json")
    assert cache.read_state(CDHASH) is None
    assert cache.remove_state(CDHASH) is True and cache.remove_state(CDHASH) is False


def test_only_well_formed_keychain_files_count(tmp_path):
    good = tmp_path / f"pal-secrets-{CDHASH}.keychain-db"
    good.write_bytes(b"")
    (tmp_path / "pal-secrets-short.keychain-db").write_bytes(b"")
    (tmp_path / "login.keychain-db").write_bytes(b"")
    (tmp_path / f"pal-secrets-{'a' * 40}.keychain-db").symlink_to(tmp_path / "login.keychain-db")

    assert cache.all_keychain_paths(tmp_path) == [(CDHASH, good)]


def test_a_null_from_core_foundation_is_refused():
    with pytest.raises(cache.KeychainCacheError, match="CFDictionaryCreate returned NULL"):
        cache._MacKeychain._created(None, "CFDictionaryCreate")
    assert cache._MacKeychain._created(1234, "CFDataCreate") == 1234
