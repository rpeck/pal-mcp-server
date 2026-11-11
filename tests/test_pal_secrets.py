"""Tests for the pal-secrets command (utils/pal_secrets.py). No test touches a real keychain, op, codesign or launchd."""

import json
import os
import plistlib
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils import op_keychain_cache as cache
from utils import pal_secrets

ACCOUNT = "my-team.1password.com"
CDHASH = "c0ffee" * 6 + "c0ff"
OTHER_CDHASH = "beef00" * 6 + "beef"
PRIVATE = cache.Identity(CDHASH, cache.PRIVATE_IDENTIFIER_PREFIX + "abc123", adhoc=True, team_id=None)
SHARED = cache.Identity(OTHER_CDHASH, "-", adhoc=True, team_id=None)
FAR_FUTURE = 4_000_000_000.0
# The real check; the keychain fixture replaces it, because the tests run in the dev venv.
REAL_REQUIRE_RUNTIME_VENV = pal_secrets._require_runtime_venv


class FakeKeychain:
    """Stands in for utils.op_keychain_cache._MacKeychain. A keychain is a real empty file in a temp folder."""

    def __init__(self):
        self.identity = PRIVATE
        self.states: dict[Path, str] = {}
        self.items: dict[Path, dict[str, bytes]] = {}
        self.created: list[tuple[Path, bytes, int]] = []
        self.finds: list[Path] = []
        self.fail_on_add = False

    def own_identity(self):
        return self.identity

    def state(self, path):
        return self.states.get(path, cache.ABSENT) if path.exists() else cache.ABSENT

    def find(self, path, account):
        self.finds.append(path)
        return self.items.get(path, {}).get(account)

    def create(self, path, password, lock_after_seconds):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        self.created.append((path, password, lock_after_seconds))
        self.states[path] = cache.UNLOCKED
        self.items[path] = {}

    def add(self, path, account, data, label):
        if self.fail_on_add:
            raise cache.KeychainCacheError("SecItemAdd failed with status -1")
        self.items[path][account] = data

    def delete(self, path):
        if not path.exists():
            return False
        path.unlink()
        self.states.pop(path, None)
        self.items.pop(path, None)
        return True

    def make(self, cdhash=CDHASH, state=cache.UNLOCKED, expires_at=FAR_FUTURE, write_state=True):
        path = cache.keychain_path(cdhash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        self.states[path] = state
        self.items[path] = {}
        if write_state:
            cache.write_state(cache.CacheState(cdhash, 0.0, expires_at, ((ACCOUNT, "op://v/i/f"),)))
        return path


@pytest.fixture()
def keychain(monkeypatch, tmp_path):
    fake = FakeKeychain()
    monkeypatch.setattr(cache, "KEYCHAIN_DIR", tmp_path / "Keychains")
    monkeypatch.setattr(cache, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cache, "backend", lambda: fake)
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", tmp_path / "pal-secrets.lock")
    monkeypatch.setattr(pal_secrets, "DELETE_GRACE_SECONDS", 0)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("PAL_OP_CACHE_REFERENCES", raising=False)
    monkeypatch.delenv("PAL_OP_ACCOUNT", raising=False)
    monkeypatch.setattr(pal_secrets, "_require_runtime_venv", lambda: Path("/venv"))
    monkeypatch.setattr(pal_secrets, "runtime_venv", lambda: Path("/venv"))
    return fake


@pytest.fixture()
def configured(monkeypatch):
    """One reference and an account; op is found; 1Password returns one value."""

    monkeypatch.setattr(
        pal_secrets, "configuration_sources", lambda: [(".env", {"K": "op://v/i/f", "PAL_OP_ACCOUNT": ACCOUNT})]
    )
    monkeypatch.setattr(pal_secrets, "find_op", lambda: "op")
    read = MagicMock(return_value={"op://v/i/f": "s3cret"})
    monkeypatch.setattr(pal_secrets, "read_secrets", read)
    return read


def _args(*argv):
    args = pal_secrets.build_parser().parse_args(list(argv))
    args.argv = list(argv)
    return args


# --- Reference collection -------------------------------------------------------------------------------------


def _views(tmp_path, dotenv: str, blocks: list[dict]):
    env_path = tmp_path / ".env"
    env_path.write_text(dotenv)
    claude_json = tmp_path / ".claude.json"
    servers = {"pal" if index == 0 else "other": {"env": block} for index, block in enumerate(blocks[:1])}
    projects = {f"/project/{index}": {"mcpServers": {"pal": {"env": block}}} for index, block in enumerate(blocks[1:])}
    claude_json.write_text(json.dumps({"mcpServers": servers, "projects": projects}))
    return pal_secrets.configuration_sources(env_path, [claude_json, tmp_path / "missing.json"])


def test_references_come_from_each_pal_process_view(tmp_path, monkeypatch):
    monkeypatch.delenv("PAL_OP_CACHE_REFERENCES", raising=False)
    views = _views(
        tmp_path,
        "GEMINI_API_KEY=op://Private/gemini/credential\n"
        "DEFAULT_MODEL=auto\n"
        "PAL_OP_ACCOUNT=my.1password.com\n"
        'PAL_OP_CACHE_REFERENCES="op://Private/extra one/credential, op://Private/extra2/credential"\n',
        [
            {
                "OPENAI_API_KEY": "op://Work/openai/credential",
                "PAL_OP_ACCOUNT_OPENAI_API_KEY": "client.1password.com",
                "LOG_LEVEL": "INFO",
            },
            {"GEMINI_API_KEY": "op://Private/gemini/credential"},
        ],
    )

    references, missing = pal_secrets.collect_references(views)

    assert list(references) == [
        ("client.1password.com", "op://Work/openai/credential"),
        ("my.1password.com", "op://Private/extra one/credential"),
        ("my.1password.com", "op://Private/extra2/credential"),
        ("my.1password.com", "op://Private/gemini/credential"),
    ]
    assert references[("my.1password.com", "op://Private/gemini/credential")] == [
        ".claude.json (pal #1) GEMINI_API_KEY",
        ".claude.json (pal #2) GEMINI_API_KEY",
    ]
    assert missing == []


@pytest.mark.parametrize(
    ("dotenv", "block", "expected"),
    [
        # The variable in .env, its account in the env block: PAL sees both in one process.
        (
            "OPENAI_API_KEY=op://w/openai/f\nPAL_OP_ACCOUNT=team\n",
            {"PAL_OP_ACCOUNT_OPENAI_API_KEY": "client"},
            [("client", "op://w/openai/f")],
        ),
        # The env block wins over .env for PAL_OP_ACCOUNT.
        (
            "OPENAI_API_KEY=op://w/openai/f\nPAL_OP_ACCOUNT=team\n",
            {"PAL_OP_ACCOUNT": "block"},
            [("block", "op://w/openai/f")],
        ),
        # With PAL_MCP_FORCE_ENV_OVERRIDE=true, PAL uses .env only.
        (
            "PAL_MCP_FORCE_ENV_OVERRIDE=true\nOPENAI_API_KEY=op://w/openai/f\nPAL_OP_ACCOUNT=team\n",
            {"PAL_OP_ACCOUNT": "block", "GEMINI_API_KEY": "op://w/gemini/f"},
            [("team", "op://w/openai/f")],
        ),
    ],
    ids=["account-in-block", "block-wins", "force-override"],
)
def test_accounts_follow_the_rules_of_pal(tmp_path, monkeypatch, dotenv, block, expected):
    monkeypatch.delenv("PAL_OP_CACHE_REFERENCES", raising=False)

    references, _ = pal_secrets.collect_references(_views(tmp_path, dotenv, [block]))

    assert list(references) == expected


def test_without_a_pal_server_the_view_is_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("PAL_OP_CACHE_REFERENCES", raising=False)

    views = _views(tmp_path, "K=op://v/i/f\nPAL_OP_ACCOUNT=team\n", [])

    assert [name for name, _ in views] == [".env"]
    assert list(pal_secrets.collect_references(views)[0]) == [("team", "op://v/i/f")]


def test_a_variable_without_an_account_is_listed_and_not_cached(monkeypatch, capsys):
    monkeypatch.delenv("PAL_OP_ACCOUNT", raising=False)
    monkeypatch.delenv("PAL_OP_CACHE_REFERENCES", raising=False)
    sources = [(".env", {"K": "op://v/i/f", "L": "op://v/i/g", "PAL_OP_ACCOUNT_L": ACCOUNT})]

    assert pal_secrets._accounted_references(sources) == {(ACCOUNT, "op://v/i/g"): [".env L"]}
    assert "not cached: .env K has no 1Password account" in capsys.readouterr().err


def test_a_broken_config_file_is_skipped_with_a_warning(tmp_path, capsys):
    broken = tmp_path / ".claude.json"
    broken.write_text("{not json")

    assert pal_secrets._pal_env_blocks(broken) == []
    assert "cannot read" in capsys.readouterr().err


def test_the_account_is_required(monkeypatch):
    monkeypatch.delenv("PAL_OP_ACCOUNT", raising=False)
    monkeypatch.delenv("PAL_OP_CACHE_REFERENCES", raising=False)

    with pytest.raises(pal_secrets.PalSecretsError, match="set PAL_OP_ACCOUNT"):
        pal_secrets._accounted_references([(".env", {"K": "op://v/i/f"})])


# --- Reading from 1Password -----------------------------------------------------------------------------------


def _fake_inject(values, returncode=0, stderr=""):
    """Return a subprocess.run replacement that answers op inject like op does."""

    def run(command, input=None, **kwargs):
        assert command[1] == "inject", f"unexpected command {command}"
        if returncode:
            return subprocess.CompletedProcess(command, returncode, "", stderr)
        output = input
        for reference, value in values.items():
            output = output.replace("{{ " + reference + " }}", value)
        return subprocess.CompletedProcess(command, 0, output, "")

    return run


def test_one_op_inject_reads_every_reference_and_keeps_line_breaks(monkeypatch):
    values = {"op://v/a/f": "first", "op://v/b/f": "line one\nline two\n"}
    run = MagicMock(side_effect=_fake_inject(values))
    monkeypatch.setattr(subprocess, "run", run)

    assert pal_secrets.read_secrets(list(values), "/bin/op", ACCOUNT, timeout=5) == values

    run.assert_called_once()
    command = run.call_args.args[0]
    assert command == ["/bin/op", "inject", "--account", ACCOUNT]
    assert all(value not in " ".join(command) for value in values.values())


def test_one_failing_reference_fails_the_whole_read(monkeypatch):
    run = MagicMock(side_effect=_fake_inject({}, returncode=1, stderr='[ERROR] could not read "op://v/bad/f"'))
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(pal_secrets.PalSecretsError, match="op://v/bad/f"):
        pal_secrets.read_secrets(["op://v/bad/f", "op://v/good/f"], "op", ACCOUNT, 5)
    # No retry: a retry can show another Touch ID prompt.
    run.assert_called_once()


def test_only_the_first_line_of_an_op_error_is_kept(monkeypatch):
    stderr = "[ERROR] first line\n" + "detail " * 200
    monkeypatch.setattr(subprocess, "run", _fake_inject({}, returncode=1, stderr=stderr))

    with pytest.raises(pal_secrets.PalSecretsError) as error:
        pal_secrets.read_secrets(["op://v/a/f"], "op", ACCOUNT, 5)

    assert str(error.value) == "op inject failed: [ERROR] first line"


def test_an_empty_value_fails_the_whole_read(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_inject({"op://v/a/f": "x", "op://v/empty/f": ""}))

    with pytest.raises(pal_secrets.PalSecretsError, match="empty value for op://v/empty/f"):
        pal_secrets.read_secrets(["op://v/a/f", "op://v/empty/f"], "op", ACCOUNT, 5)


def test_split_injected_needs_every_marker_and_no_marker_in_a_value():
    output = "m1:0\nfirst\nm1:1\nsecond\nm1:end\n"

    assert pal_secrets._split_injected(output, "m1", ["a", "b"]) == {"a": "first", "b": "second"}
    assert pal_secrets._split_injected("m1:0\nfirst\n", "m1", ["a", "b"]) is None
    assert pal_secrets._split_injected("m1:0\nhas m1 inside\nm1:end\n", "m1", ["a"]) is None


# --- refresh ----------------------------------------------------------------------------------------------------


def test_refresh_replaces_every_cache_and_writes_the_state_file_last(keychain, configured, capsys):
    old_own, old_other = keychain.make(), keychain.make(OTHER_CDHASH)
    before = time.time()

    assert pal_secrets.command_refresh(_args("refresh", "--ttl-minutes", "90")) == 0

    path = cache.keychain_path(CDHASH)
    assert not old_other.exists() and not cache.state_path(OTHER_CDHASH).exists()
    assert path == old_own and path.exists()
    ((created_path, password, lock_after),) = keychain.created
    assert created_path == path and len(password) >= 32
    assert lock_after == 90 * 60 + cache.LOCK_AFTER_TTL_SECONDS, "the keychain locks only after the TTL"
    value, expires_at = cache.decode_item(
        keychain.items[path][cache.item_account(ACCOUNT, "op://v/i/f")], ACCOUNT, "op://v/i/f"
    )
    assert value == "s3cret"
    assert before + 90 * 60 <= expires_at <= time.time() + 90 * 60
    state = cache.read_state(CDHASH)
    assert state.entries == ((ACCOUNT, "op://v/i/f"),) and state.expires_at == expires_at
    assert keychain.finds == [path], "refresh reads one item back"
    output = capsys.readouterr()
    assert "s3cret" not in output.out + output.err


def test_refresh_reads_each_account_with_its_own_op_inject(monkeypatch, keychain, configured):
    variables = {
        "K": "op://v/i/f",
        "L": "op://w/i/f",
        "PAL_OP_ACCOUNT": ACCOUNT,
        "PAL_OP_ACCOUNT_L": "client.1password.com",
    }
    monkeypatch.setattr(pal_secrets, "configuration_sources", lambda: [(".env", variables)])
    configured.side_effect = lambda refs, op, account, timeout: dict.fromkeys(refs, f"value-{account}")

    assert pal_secrets.command_refresh(_args("refresh")) == 0

    assert [call.args[2] for call in configured.call_args_list] == ["client.1password.com", ACCOUNT]
    assert cache.read_state(CDHASH).entries == (("client.1password.com", "op://w/i/f"), (ACCOUNT, "op://v/i/f"))
    path = cache.keychain_path(CDHASH)
    item = keychain.items[path][cache.item_account("client.1password.com", "op://w/i/f")]
    assert cache.decode_item(item, "client.1password.com", "op://w/i/f")[0] == "value-client.1password.com"


def test_refresh_is_all_or_nothing_over_the_accounts(monkeypatch, keychain, configured):
    old = keychain.make()
    variables = {
        "K": "op://v/i/f",
        "L": "op://w/i/f",
        "PAL_OP_ACCOUNT": ACCOUNT,
        "PAL_OP_ACCOUNT_L": "client.1password.com",
    }
    monkeypatch.setattr(pal_secrets, "configuration_sources", lambda: [(".env", variables)])

    def second_account_fails(refs, op, account, timeout):
        if account == ACCOUNT:
            raise pal_secrets.PalSecretsError("op inject failed: not signed in")
        return dict.fromkeys(refs, "value")

    configured.side_effect = second_account_fails

    with pytest.raises(pal_secrets.PalSecretsError, match="not signed in"):
        pal_secrets.command_refresh(_args("refresh"))

    assert old.exists() and keychain.created == []


def test_refresh_keeps_the_old_cache_when_1password_fails(keychain, configured):
    old = keychain.make()
    configured.side_effect = pal_secrets.PalSecretsError("op inject failed: timeout")

    with pytest.raises(pal_secrets.PalSecretsError, match="timeout"):
        pal_secrets.command_refresh(_args("refresh"))

    assert old.exists() and cache.read_state(CDHASH) is not None and keychain.created == []


def test_refresh_leaves_no_keychain_when_create_fails(monkeypatch, keychain, configured):
    def create_then_fail(path, password, lock_after_seconds):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        raise cache.KeychainCacheError("SecKeychainSetSettings failed with status -1")

    monkeypatch.setattr(keychain, "create", create_then_fail)

    with pytest.raises(cache.KeychainCacheError):
        pal_secrets.command_refresh(_args("refresh"))

    assert not cache.keychain_path(CDHASH).exists() and cache.read_state(CDHASH) is None


def test_refresh_deletes_a_half_written_cache(keychain, configured):
    keychain.fail_on_add = True

    with pytest.raises(cache.KeychainCacheError):
        pal_secrets.command_refresh(_args("refresh"))

    assert not cache.keychain_path(CDHASH).exists() and cache.read_state(CDHASH) is None


def test_refresh_needs_references_and_an_account(monkeypatch, keychain):
    monkeypatch.setattr(pal_secrets, "configuration_sources", lambda: [(".env", {"PAL_OP_ACCOUNT": ACCOUNT})])
    with pytest.raises(pal_secrets.PalSecretsError, match="found no op:// references"):
        pal_secrets.command_refresh(_args("refresh"))

    monkeypatch.setattr(pal_secrets, "configuration_sources", lambda: [(".env", {"K": "op://v/i/f"})])
    with pytest.raises(pal_secrets.PalSecretsError, match="set PAL_OP_ACCOUNT"):
        pal_secrets.command_refresh(_args("refresh"))


@pytest.mark.parametrize(
    ("hours_left", "state", "refreshed"),
    [(20, cache.UNLOCKED, False), (11, cache.UNLOCKED, True), (20, cache.LOCKED, True)],
    ids=["valid", "less-than-12-hours", "locked"],
)
def test_refresh_if_needed(keychain, configured, hours_left, state, refreshed):
    keychain.make(state=state, expires_at=time.time() + hours_left * 3600)

    assert pal_secrets.command_refresh(_args("refresh", "--if-needed")) == 0

    assert configured.called is refreshed


def _hold_lock(seconds: float) -> threading.Thread:
    """Hold the lock in another thread (another open file), as expire in another process would."""

    taken = threading.Event()

    def hold():
        with pal_secrets._exclusive_lock(timeout=None):
            taken.set()
            time.sleep(seconds)

    thread = threading.Thread(target=hold)
    thread.start()
    taken.wait()
    return thread


def test_pal_secrets_refuses_another_venv(monkeypatch, tmp_path):
    (tmp_path / ".pal_venv").mkdir()
    monkeypatch.setattr(pal_secrets, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(pal_secrets, "venv_dir", lambda: tmp_path / ".dev_venv")

    with pytest.raises(pal_secrets.PalSecretsError, match="run pal-secrets with PAL's Python"):
        REAL_REQUIRE_RUNTIME_VENV()

    monkeypatch.setattr(pal_secrets, "venv_dir", lambda: (tmp_path / ".pal_venv").resolve())
    assert REAL_REQUIRE_RUNTIME_VENV() == (tmp_path / ".pal_venv").resolve()


def test_refresh_if_needed_runs_when_a_reference_is_new(keychain, configured):
    keychain.make(expires_at=time.time() + 20 * 3600)
    cache.write_state(cache.CacheState(CDHASH, 0.0, time.time() + 20 * 3600, ((ACCOUNT, "op://v/i/old"),)))

    assert pal_secrets.command_refresh(_args("refresh", "--if-needed")) == 0

    configured.assert_called_once()


def test_refresh_waits_while_expire_holds_the_lock(keychain, configured):
    # At login, launchd starts expire and refresh together.
    thread = _hold_lock(0.5)

    assert pal_secrets.command_refresh(_args("refresh")) == 0

    thread.join()
    configured.assert_called_once()
    assert cache.read_state(CDHASH) is not None


def test_refresh_fails_loudly_when_the_lock_stays_busy(monkeypatch, keychain, configured):
    monkeypatch.setattr(pal_secrets, "LOCK_WAIT_SECONDS", 0.3)
    notify = MagicMock()
    monkeypatch.setattr(pal_secrets, "_notify", notify)
    thread = _hold_lock(1.0)

    assert pal_secrets.main(["refresh", "--notify"]) == 1

    thread.join()
    configured.assert_not_called()
    notify.assert_called_once()


def test_refresh_repairs_the_private_copy_and_restarts_in_it(monkeypatch, keychain, configured):
    keychain.identity = SHARED
    monkeypatch.delenv(pal_secrets._REEXEC_MARK, raising=False)
    monkeypatch.setattr(pal_secrets, "venv_dir", lambda: Path("/venv"))
    install = MagicMock(return_value=Path("/venv/bin/python"))
    monkeypatch.setattr(pal_secrets, "install_private_python", install)
    execve = MagicMock(side_effect=SystemExit(0))
    monkeypatch.setattr(pal_secrets.os, "execve", execve)

    with pytest.raises(SystemExit):
        pal_secrets.command_refresh(_args("refresh", "--if-needed"))

    install.assert_called_once_with(Path("/venv"))
    path, argv, env = execve.call_args.args
    assert path == "/venv/bin/python" and argv[0] == path and argv[-2:] == ["refresh", "--if-needed"]
    assert env[pal_secrets._REEXEC_MARK] == "1"
    configured.assert_not_called()


def test_refresh_gives_up_when_the_restarted_process_is_still_not_private(monkeypatch, keychain, configured):
    keychain.identity = SHARED
    monkeypatch.setenv(pal_secrets._REEXEC_MARK, "1")

    with pytest.raises(pal_secrets.PalSecretsError, match="does not work"):
        pal_secrets.command_refresh(_args("refresh"))


# --- expire and clear -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "deleted"),
    [
        (lambda fake: fake.make(state=cache.LOCKED), True),
        (lambda fake: fake.make(expires_at=1.0), True),
        (lambda fake: fake.make(write_state=False), True),
        (lambda fake: fake.make(), False),
        (lambda fake: fake.make(OTHER_CDHASH, state=cache.LOCKED), True),
        (lambda fake: fake.make(OTHER_CDHASH), False),
    ],
    ids=["locked", "expired", "no-state-file", "valid", "other-binary-locked", "other-binary-valid"],
)
def test_expire(keychain, make, deleted):
    path = make(keychain)

    assert pal_secrets.command_expire(None) == 0

    assert path.exists() is not deleted
    assert keychain.finds == [], "expire never reads an item"


def test_expire_deletes_any_cache_older_than_24_hours(monkeypatch, keychain):
    own, other = keychain.make(), keychain.make(OTHER_CDHASH)
    monkeypatch.setattr(pal_secrets, "_created_at", lambda path: time.time() - cache.MAX_TTL_SECONDS - 1)

    pal_secrets.command_expire(None)

    assert not own.exists() and not other.exists()
    assert not list(cache.STATE_DIR.glob("*.json"))


def test_expire_removes_the_state_file_before_the_grace_wait(monkeypatch, keychain):
    path = keychain.make(expires_at=1.0)
    seen = []
    monkeypatch.setattr(pal_secrets.time, "sleep", lambda seconds: seen.append(cache.state_path(CDHASH).exists()))

    pal_secrets.command_expire(None)

    assert seen == [False], "PAL stops reading before the keychain goes"
    assert not path.exists()


def test_expire_skips_while_a_refresh_runs(keychain, capsys):
    path = keychain.make(expires_at=1.0)

    with pal_secrets._exclusive_lock(timeout=None):
        pal_secrets.command_expire(None)

    assert path.exists() and "skipped" in capsys.readouterr().out


def test_expire_writes_to_the_log_only_when_it_deletes(keychain, capsys):
    keychain.make()

    assert pal_secrets.main(["expire"]) == 0
    assert capsys.readouterr().out == ""

    keychain.make(expires_at=1.0)
    assert pal_secrets.main(["expire"]) == 0
    out = capsys.readouterr().out
    assert "pal-secrets expire" in out and "(expired)" in out


def test_clear_deletes_every_cache_and_state_file(keychain, capsys):
    paths = [keychain.make(), keychain.make(OTHER_CDHASH)]
    cache.write_state(cache.CacheState("f" * 40, 0.0, FAR_FUTURE, ()))

    assert pal_secrets.command_clear(None) == 0

    assert not any(path.exists() for path in paths)
    assert not list(cache.STATE_DIR.glob("*.json"))
    assert pal_secrets.command_clear(None) == 0
    assert "There is no cache to delete." in capsys.readouterr().out


# --- The private Python copy -------------------------------------------------------------------------------------


@pytest.fixture()
def venv(tmp_path):
    """A venv whose bin/python links to an interpreter that loads ../lib/libpython3.13.dylib."""

    source = tmp_path / "cpython-3.13.3" / "bin" / "python3.13"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"binary")
    (tmp_path / "cpython-3.13.3" / "lib").mkdir()
    (tmp_path / "cpython-3.13.3" / "lib" / "libpython3.13.dylib").write_bytes(b"library")
    # Newer uv links a venv to a directory for the minor version, which points to the current patch release.
    (tmp_path / "cpython-3.13").symlink_to(tmp_path / "cpython-3.13.3")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "lib").mkdir()
    (venv / "bin" / "python").symlink_to(tmp_path / "cpython-3.13" / "bin" / "python3.13")
    return venv


def _fake_codesign(signed: dict, started_identity=None):
    """Answer codesign and the start check of the copy like macOS does."""

    def run(command, **kwargs):
        if command[:2] == ["codesign", "--force"]:
            signed[Path(command[-1]).name] = command[command.index("--identifier") + 1]
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["codesign", "-dv"]:
            # The signature moves with the file when the copy replaces bin/python.
            identifier = next(iter(signed.values()), "-")
            return subprocess.CompletedProcess(command, 0, "", f"Identifier={identifier}\n")
        if command[1] == "-c":
            identity = started_identity or [signed[Path(command[0]).name], True, None]
            return subprocess.CompletedProcess(command, 0, json.dumps(identity) + "\n", "")
        raise AssertionError(f"unexpected command {command}")

    return run


def test_install_private_python_and_restore_the_link(monkeypatch, venv):
    monkeypatch.setattr(subprocess, "run", _fake_codesign({}))
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", venv.parent / "pal-secrets.lock")
    python = venv / "bin" / "python"
    link_text = os.readlink(python)
    source = python.resolve()

    assert pal_secrets.install_private_python(venv) == python

    assert python.is_file() and not python.is_symlink() and python.read_bytes() == b"binary"
    assert pal_secrets.private_identifier(venv).startswith(cache.PRIVATE_IDENTIFIER_PREFIX)
    assert (venv / "lib" / "libpython3.13.dylib").resolve() == source.parent.parent / "lib" / "libpython3.13.dylib"
    assert json.loads((venv / pal_secrets.PRIVATE_PYTHON_RECORD).read_text())["source"] == str(source)
    assert pal_secrets.install_private_python(venv) == python, "a second run keeps the copy"
    # The library link goes through the minor-version directory, not the patch release.
    assert "cpython-3.13/" in os.readlink(venv / "lib" / "libpython3.13.dylib")

    assert pal_secrets.restore_python_link(venv) is True

    assert python.is_symlink() and os.readlink(python) == link_text, "the link text, not the resolved path"
    assert not (venv / "lib" / "libpython3.13.dylib").exists()
    assert not (venv / pal_secrets.PRIVATE_PYTHON_RECORD).exists()


def test_a_failed_replace_leaves_no_record(monkeypatch, venv):
    monkeypatch.setattr(subprocess, "run", _fake_codesign({}))
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", venv.parent / "pal-secrets.lock")

    def fail(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(pal_secrets.os, "replace", fail)

    with pytest.raises(OSError):
        pal_secrets.install_private_python(venv)

    assert (venv / "bin" / "python").is_symlink()
    assert not (venv / pal_secrets.PRIVATE_PYTHON_RECORD).exists()
    assert not (venv / "lib" / "libpython3.13.dylib").exists()


def test_a_library_link_that_already_exists_is_recorded(monkeypatch, venv):
    monkeypatch.setattr(subprocess, "run", _fake_codesign({}))
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", venv.parent / "pal-secrets.lock")
    library = (venv / "bin" / "python").resolve().parent.parent / "lib" / "libpython3.13.dylib"
    (venv / "lib" / "libpython3.13.dylib").symlink_to(library)

    pal_secrets.install_private_python(venv)
    pal_secrets.restore_python_link(venv)

    assert not (venv / "lib" / "libpython3.13.dylib").exists()


def test_two_installs_at_the_same_time_leave_a_working_copy(monkeypatch, venv):
    monkeypatch.setattr(subprocess, "run", _fake_codesign({}))
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", venv.parent / "pal-secrets.lock")
    real_copy = pal_secrets.shutil.copy2

    def slow_copy(source, target):
        time.sleep(0.2)
        return real_copy(source, target)

    monkeypatch.setattr(pal_secrets.shutil, "copy2", slow_copy)
    errors = []

    def install():
        try:
            pal_secrets.install_private_python(venv)
        except Exception as exc:  # noqa: BLE001 - the test reports it
            errors.append(exc)

    threads = [threading.Thread(target=install) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    python = venv / "bin" / "python"
    assert python.is_file() and not python.is_symlink()
    assert (venv / "lib" / "libpython3.13.dylib").is_symlink(), "the copy can load its library"
    assert sorted(path.name for path in (venv / "bin").iterdir()) == ["python"], "no temporary file stays"


def test_a_python_that_starts_another_binary_is_refused(monkeypatch, venv):
    monkeypatch.setattr(subprocess, "run", _fake_codesign({}, started_identity=["org.python.python", False, "TEAM"]))
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", venv.parent / "pal-secrets.lock")

    with pytest.raises(pal_secrets.PalSecretsError, match="runs another binary"):
        pal_secrets.install_private_python(venv)

    assert (venv / "bin" / "python").is_symlink(), "the venv stays as it was"
    assert not (venv / "lib" / "libpython3.13.dylib").exists()
    assert list((venv / "bin").iterdir()) == [venv / "bin" / "python"]


def test_a_real_file_that_is_not_the_copy_is_never_replaced(monkeypatch, venv):
    python = venv / "bin" / "python"
    python.unlink()
    python.write_bytes(b"someone else's binary")
    monkeypatch.setattr(subprocess, "run", _fake_codesign({}))
    monkeypatch.setattr(pal_secrets, "LOCK_PATH", venv.parent / "pal-secrets.lock")

    with pytest.raises(pal_secrets.PalSecretsError, match="not a symbolic link"):
        pal_secrets.install_private_python(venv)
    assert python.read_bytes() == b"someone else's binary"


# --- init, uninstall, main --------------------------------------------------------------------------------------


def test_init_installs_two_launch_agents_that_run_the_private_copy(monkeypatch, keychain, configured, tmp_path, capsys):
    agents = tmp_path / "LaunchAgents"
    monkeypatch.setattr(pal_secrets, "LAUNCH_AGENTS_DIR", agents)
    monkeypatch.setattr(pal_secrets, "LOG_PATH", tmp_path / "Logs" / "pal-secrets.log")
    monkeypatch.setattr(pal_secrets, "find_op", lambda: "/opt/op-folder/op")
    monkeypatch.setattr(pal_secrets, "_require_runtime_venv", lambda: tmp_path / "venv")
    monkeypatch.setattr(pal_secrets, "install_private_python", lambda venv: venv / "bin" / "python")
    launchctl = MagicMock(return_value=subprocess.CompletedProcess(["launchctl"], 0, "", ""))
    monkeypatch.setattr(pal_secrets, "_launchctl", launchctl)

    assert pal_secrets.main(["init", "--refresh-at", "7:05"]) == 0

    with open(agents / f"{pal_secrets.REFRESH_LABEL}.plist", "rb") as handle:
        refresh = plistlib.load(handle)
    with open(agents / f"{pal_secrets.EXPIRE_LABEL}.plist", "rb") as handle:
        expire = plistlib.load(handle)
    python = str(tmp_path / "venv" / "bin" / "python")
    assert refresh["ProgramArguments"] == [python, str(pal_secrets.SCRIPT_PATH), "refresh", "--if-needed", "--notify"]
    assert refresh["StartCalendarInterval"] == {"Hour": 7, "Minute": 5} and refresh["RunAtLoad"] is True
    assert refresh["EnvironmentVariables"]["PATH"].startswith("/opt/op-folder:")
    assert "ProcessType" not in refresh
    assert expire["ProgramArguments"][2:] == ["expire"]
    assert expire["StartInterval"] == 15 * 60 and expire["RunAtLoad"] is True
    bootstraps = [call.args for call in launchctl.call_args_list if call.args[0] == "bootstrap"]
    assert len(bootstraps) == 2
    assert "Next steps" in capsys.readouterr().out


def test_init_needs_the_account(monkeypatch, keychain, tmp_path):
    monkeypatch.setattr(pal_secrets, "venv_dir", lambda: tmp_path / "venv")
    monkeypatch.setattr(pal_secrets, "configuration_sources", lambda: [])
    install = MagicMock()
    monkeypatch.setattr(pal_secrets, "install_private_python", install)

    assert pal_secrets.main(["init"]) == 1
    install.assert_not_called()


def test_uninstall_removes_the_agents_the_cache_and_the_copy(monkeypatch, keychain, tmp_path):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    for label in (pal_secrets.REFRESH_LABEL, pal_secrets.EXPIRE_LABEL):
        (agents / f"{label}.plist").write_bytes(b"")
    monkeypatch.setattr(pal_secrets, "LAUNCH_AGENTS_DIR", agents)
    monkeypatch.setattr(pal_secrets, "_launchctl", MagicMock())
    monkeypatch.setattr(pal_secrets, "runtime_venv", lambda: tmp_path / "venv")
    restore = MagicMock(return_value=True)
    monkeypatch.setattr(pal_secrets, "restore_python_link", restore)
    path = keychain.make()

    assert pal_secrets.main(["uninstall"]) == 0

    assert list(agents.iterdir()) == [] and not path.exists()
    restore.assert_called_once_with(tmp_path / "venv")


def test_uninstall_reports_a_failed_restore(monkeypatch, keychain, tmp_path, capsys):
    monkeypatch.setattr(pal_secrets, "LAUNCH_AGENTS_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(pal_secrets, "_launchctl", MagicMock())
    monkeypatch.setattr(
        pal_secrets, "restore_python_link", MagicMock(side_effect=pal_secrets.PalSecretsError("lock is busy"))
    )

    assert pal_secrets.main(["uninstall"]) == 1
    assert "was not restored: lock is busy" in capsys.readouterr().err


def test_ttl_cannot_exceed_24_hours():
    with pytest.raises(SystemExit):
        pal_secrets.build_parser().parse_args(["refresh", "--ttl-minutes", str(24 * 60 + 1)])


def test_main_refuses_to_run_outside_macos(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")

    assert pal_secrets.main(["status"]) == 2
    assert "needs macOS" in capsys.readouterr().err


def test_main_notifies_when_the_launchd_refresh_fails(monkeypatch, keychain):
    monkeypatch.setattr(pal_secrets, "configuration_sources", lambda: [])
    notify = MagicMock()
    monkeypatch.setattr(pal_secrets, "_notify", notify)

    assert pal_secrets.main(["refresh", "--notify"]) == 1
    notify.assert_called_once()
    assert pal_secrets.main(["refresh"]) == 1
    notify.assert_called_once()


def test_an_unexpected_error_still_notifies_without_its_text(monkeypatch, keychain, configured, capsys):
    configured.side_effect = ValueError("s3cret-looking text")
    notify = MagicMock()
    monkeypatch.setattr(pal_secrets, "_notify", notify)

    assert pal_secrets.main(["refresh", "--notify"]) == 1

    err = capsys.readouterr().err
    assert "unexpected ValueError at " in err and "s3cret" not in err
    notify.assert_called_once()
    assert "s3cret" not in notify.call_args.args[0]


def test_the_notification_text_is_an_argument_not_script(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(subprocess, "run", run)

    pal_secrets._notify('a "quoted" text')

    command = run.call_args.args[0]
    assert command[0] == "osascript" and command[-1] == 'a "quoted" text'
    assert all('"quoted"' not in part for part in command[:-1])
