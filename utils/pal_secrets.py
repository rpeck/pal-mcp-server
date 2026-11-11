"""pal-secrets: manage the optional macOS Keychain cache of 1Password secrets (PAL_OP_CACHE=keychain).

Run it with the Python of PAL's virtual environment: ``.pal_venv/bin/python scripts/pal_secrets.py <command>``.
Commands:

- ``init``: give the venv a private Python copy, and install two LaunchAgents (``refresh`` every morning and at
  login, ``expire`` every 15 minutes).
- ``refresh``: read every cached reference from 1Password in one ``op`` process, then write a new keychain.
- ``status``: show the state of the cache. Never shows a value.
- ``expire``: delete the cache if it has expired or locked.
- ``clear``: delete the cache now.
- ``uninstall``: remove the LaunchAgents, delete the cache, and restore the venv's Python link.

See docs/1password.md, "Keychain cache (macOS)".
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path

from utils import op_keychain_cache as cache

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - python-dotenv is a PAL dependency
    dotenv_values = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "pal_secrets.py"
CLAUDE_CODE_CONFIG = Path.home() / ".claude.json"
CLAUDE_DESKTOP_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
PAL_SERVER_NAME = "pal"

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
REFRESH_LABEL = "org.pal-mcp-server.pal-secrets.refresh"
EXPIRE_LABEL = "org.pal-mcp-server.pal-secrets.expire"
EXPIRE_INTERVAL_SECONDS = 15 * 60
LOG_PATH = Path.home() / "Library" / "Logs" / "pal-secrets.log"
LOCK_PATH = Path.home() / "Library" / "Caches" / "pal-secrets.lock"

# Before a delete, wait this long, so that a PAL read that started before the state file went away can end.
DELETE_GRACE_SECONDS = 2
# `refresh --if-needed` (launchd, also at login) keeps a cache that stays valid for more than this time.
IF_NEEDED_SECONDS = 12 * 60 * 60
# How long refresh and the Python copy install wait for another pal-secrets command (for example expire).
LOCK_WAIT_SECONDS = 30
# The venv keeps a record of its private Python copy here, so that `uninstall` can restore the link.
PRIVATE_PYTHON_RECORD = "pal-secrets-python.json"
_REEXEC_MARK = "PAL_SECRETS_PRIVATE_PYTHON"

# Variables that configure op or the cache. They are never cached. PAL_OP_ACCOUNT_<KEY> is one too.
_SETTINGS = {"PAL_OP_ACCOUNT", "PAL_OP_TIMEOUT_SECONDS", "PAL_OP_CACHE", "PAL_OP_CACHE_REFERENCES"}
_ACCOUNT_PREFIX = "PAL_OP_ACCOUNT_"
_DEFAULT_OP_TIMEOUT_SECONDS = 300
_OP_SEARCH_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


class PalSecretsError(Exception):
    """A failure that ends the command with a message. The message never holds a secret value."""


# --- Configuration sources -----------------------------------------------------------------------------------


def _read_dotenv(path: Path) -> dict[str, str]:
    if dotenv_values is None or not path.exists():
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _pal_env_blocks(config_path: Path) -> list[dict[str, str]]:
    """Return the ``env`` blocks of every MCP server named ``pal`` in a Claude Code or Claude Desktop config."""

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        print(f"warning: cannot read {config_path}: {exc}", file=sys.stderr)
        return []
    servers = [config.get("mcpServers", {})]
    # Claude Code also keeps servers per project directory.
    servers += [project.get("mcpServers", {}) for project in config.get("projects", {}).values()]
    blocks = []
    for server_map in servers:
        server = server_map.get(PAL_SERVER_NAME) if isinstance(server_map, dict) else None
        env = server.get("env") if isinstance(server, dict) else None
        if isinstance(env, dict):
            blocks.append({key: value for key, value in env.items() if isinstance(value, str)})
    return blocks


def configuration_sources(
    env_path: Path = ENV_PATH,
    config_paths: Iterable[Path] = (CLAUDE_CODE_CONFIG, CLAUDE_DESKTOP_CONFIG),
) -> list[tuple[str, dict[str, str]]]:
    """Return ``(name, variables)`` for each PAL process: the variables as that process sees them.

    This follows utils/env.py. An MCP client starts PAL with its ``env`` block, and ``.env`` fills the keys that the
    block does not set. With ``PAL_MCP_FORCE_ENV_OVERRIDE=true`` in ``.env``, PAL uses ``.env`` only. Without a
    ``pal`` server in a client configuration, PAL sees ``.env``.
    """

    dotenv = _read_dotenv(env_path)
    force = (dotenv.get("PAL_MCP_FORCE_ENV_OVERRIDE") or "").strip().lower() == "true"
    views = []
    for path in config_paths:
        for index, block in enumerate(_pal_env_blocks(path)):
            variables = dict(dotenv) if force else {**dotenv, **block}
            views.append((f"{path.name} ({PAL_SERVER_NAME} #{index + 1})", variables))
    return views or [(".env", dotenv)]


def collect_references(
    views: Sequence[tuple[str, Mapping[str, str]]],
) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    """Return each ``(account, reference)`` to cache with the places that use it, and the variables without an account.

    ``views`` comes from configuration_sources: the variables of each PAL process. In each view, the account of a
    variable is ``PAL_OP_ACCOUNT_<KEY>``, else ``PAL_OP_ACCOUNT``, as in utils/env.py. ``PAL_OP_CACHE_REFERENCES``
    (in a view or in the environment of refresh) adds references with the view's ``PAL_OP_ACCOUNT``. The list uses
    commas or line breaks, not spaces, because a 1Password name can hold a space.
    """

    references: dict[tuple[str, str], list[str]] = {}
    missing: list[str] = []

    def add(account: str | None, reference: str, source: str) -> None:
        reference = reference.strip()
        if not reference.startswith("op://"):
            return
        if not account:
            if source not in missing:
                missing.append(source)
            return
        names = references.setdefault((account, reference), [])
        if source not in names:
            names.append(source)

    environment_list = os.environ.get("PAL_OP_CACHE_REFERENCES", "")
    for view, variables in views:
        default = variables.get("PAL_OP_ACCOUNT") or None
        for reference in re.split(r"[,\n]+", variables.get("PAL_OP_CACHE_REFERENCES", "")):
            add(default, reference, f"{view} PAL_OP_CACHE_REFERENCES")
        for reference in re.split(r"[,\n]+", environment_list):
            add(default, reference, f"{view} PAL_OP_CACHE_REFERENCES (environment)")
        for key, value in variables.items():
            if key in _SETTINGS or key.startswith(_ACCOUNT_PREFIX):
                continue
            add(variables.get(_ACCOUNT_PREFIX + key) or default, value, f"{view} {key}")
    return dict(sorted(references.items())), missing


def _first_setting(views: Sequence[tuple[str, Mapping[str, str]]], key: str) -> str | None:
    for _, variables in views:
        if variables.get(key):
            return variables[key]
    return None


def _accounted_references(sources: Sequence[tuple[str, Mapping[str, str]]]) -> dict[tuple[str, str], list[str]]:
    """Return the references to cache. Warn about variables without an account; fail if nothing is left."""

    references, missing = collect_references(sources)
    for name in missing:
        print(
            f"warning: not cached: {name} has no 1Password account; set PAL_OP_ACCOUNT or "
            f"{_ACCOUNT_PREFIX}<variable>",
            file=sys.stderr,
        )
    if not references and missing:
        raise PalSecretsError(
            "set PAL_OP_ACCOUNT (the 1Password sign-in address or account ID) in .env or in the env block of "
            "the pal server: each cached value belongs to one account"
        )
    return references


# --- 1Password --------------------------------------------------------------------------------------------------


def find_op() -> str:
    path = shutil.which("op") or shutil.which("op", path=_OP_SEARCH_PATH)
    if not path:
        raise PalSecretsError("the op command is not on PATH; install the 1Password CLI (see docs/1password.md)")
    return path


def read_secrets(references: list[str], op_path: str, account: str, timeout: int) -> dict[str, str]:
    """Read all references with one ``op inject`` process, so that one approval covers all of them.

    The template goes to op on stdin and the values come back on stdout, both through pipes; nothing is on a
    command line, in a file or in an environment. Random markers separate the values, so a value can hold any
    text. All or nothing: if one reference fails, raise PalSecretsError with op's error text (no values).
    """

    marker = secrets.token_hex(16)
    template = "".join(f"{marker}:{index}\n{{{{ {ref} }}}}\n" for index, ref in enumerate(references))
    template += f"{marker}:end\n"
    command = [op_path, "inject", "--account", account]
    try:
        result = subprocess.run(
            command, input=template, capture_output=True, text=True, encoding="utf-8", timeout=timeout, check=False
        )
    except UnicodeDecodeError:
        raise PalSecretsError("op returned output that is not UTF-8") from None
    except FileNotFoundError:
        raise PalSecretsError("the op command is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise PalSecretsError(f"op did not finish within {timeout}s (nobody approved the prompt?)") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalSecretsError(f"cannot run op: {exc}") from None
    if result.returncode != 0:
        error = (result.stderr.strip().splitlines() or [f"exit status {result.returncode}"])[0][:300]
        raise PalSecretsError(f"op inject failed: {error}")
    values = _split_injected(result.stdout, marker, references)
    if values is None:
        raise PalSecretsError("op inject gave output in an unexpected format")
    empty = [reference for reference, value in values.items() if not value]
    if empty:
        raise PalSecretsError(f"1Password returned an empty value for {', '.join(empty)}")
    return values


def _split_injected(output: str, marker: str, references: list[str]) -> dict[str, str] | None:
    """Split the output of ``op inject`` at the markers. Return None if the output does not match."""

    values: dict[str, str] = {}
    position = 0
    for index, reference in enumerate(references):
        start_marker = f"{marker}:{index}\n"
        end_marker = f"\n{marker}:{index + 1}\n" if index + 1 < len(references) else f"\n{marker}:end\n"
        start = output.find(start_marker, position)
        if start < 0:
            return None
        start += len(start_marker)
        end = output.find(end_marker, start)
        if end < 0:
            return None
        values[reference] = output[start:end]
        position = end + 1
    # A value that holds a marker would move the split. The markers are random, so this means bad output.
    if any(marker in value for value in values.values()):
        return None
    return values


# --- The private Python copy -------------------------------------------------------------------------------------


def venv_dir() -> Path:
    if sys.prefix == sys.base_prefix:
        raise PalSecretsError("run pal-secrets with the Python of PAL's virtual environment (.pal_venv/bin/python)")
    return Path(sys.prefix).resolve()


def runtime_venv() -> Path:
    """Return PAL's venv: ``.pal_venv`` next to this code, else the venv of this process."""

    pal_venv = PROJECT_ROOT / ".pal_venv"
    return pal_venv.resolve() if pal_venv.is_dir() else venv_dir()


def _require_runtime_venv() -> Path:
    """Return the venv of this process. Refuse any venv that is not PAL's, for example the dev venv.

    The private copy and the cache belong to PAL's Python: run from another venv, refresh would change that venv
    and delete PAL's cache.
    """

    venv, runtime = venv_dir(), runtime_venv()
    if venv != runtime:
        raise PalSecretsError(
            f"run pal-secrets with PAL's Python, not the one in {venv}: {runtime / 'bin' / 'python'} {SCRIPT_PATH}"
        )
    return venv


def private_identifier(venv: Path) -> str:
    """Return the signing identifier of the private copy. It differs per venv, so every copy has its own cdhash."""

    return cache.PRIVATE_IDENTIFIER_PREFIX + hashlib.sha256(str(venv).encode("utf-8")).hexdigest()[:12]


def signature_identifier(path: Path) -> str | None:
    result = subprocess.run(["codesign", "-dv", str(path)], capture_output=True, text=True, check=False)
    for line in result.stderr.splitlines():
        if line.startswith("Identifier="):
            return line.split("=", 1)[1]
    return None


def install_private_python(venv: Path) -> Path:
    """Replace the venv's ``bin/python`` link with a copy of the binary, re-signed ad-hoc with its own identifier.

    Each script that runs on one binary has the same cdhash, and the cdhash decides who can read a keychain
    item. The shared interpreter (for example uv's) also runs other projects. The private copy gives PAL's venv
    a cdhash of its own, and every launcher of ``.pal_venv/bin/python`` uses it with no change.
    """

    # Two installs at the same time (for example run-server.sh and a launchd refresh) could break bin/python.
    with _exclusive_lock(timeout=LOCK_WAIT_SECONDS) as locked:
        if not locked:
            raise PalSecretsError("another pal-secrets command holds the lock; try again")
        return _install_private_python(venv)


def _install_private_python(venv: Path) -> Path:
    python = venv / "bin" / "python"
    identifier = private_identifier(venv)
    if python.is_file() and not python.is_symlink():
        if signature_identifier(python) == identifier:
            return python
        raise PalSecretsError(f"{python} is not a symbolic link, so pal-secrets does not replace it")
    if not python.is_symlink():
        raise PalSecretsError(f"{python} does not exist")
    # Keep the text of the link: uv can link to a directory for a minor version, which later points elsewhere.
    link_text = os.readlink(python)
    # The library is linked through this path, not the resolved one: a uv directory for a minor version then
    # keeps working after a patch upgrade.
    unresolved = Path(os.path.normpath(python.parent / link_text))
    source = python.resolve()
    links: list[str] = []
    created: list[Path] = []
    handle, temp_name = tempfile.mkstemp(dir=python.parent, prefix=".python.pal-secrets-")
    os.close(handle)
    new = Path(temp_name)
    try:
        # The binary can load libpython from @executable_path/../lib. Give the copy the same library.
        for library in sorted((unresolved.parent.parent / "lib").glob("libpython3*.dylib")):
            link = venv / "lib" / library.name
            links.append(str(link))
            if link.is_symlink() and link.resolve() == library.resolve():
                continue
            if link.exists() or link.is_symlink():
                raise PalSecretsError(f"{link} already exists; pal-secrets does not replace it")
            link.symlink_to(library)
            created.append(link)
        shutil.copy2(source, new)
        signed = subprocess.run(
            ["codesign", "--force", "--sign", "-", "--identifier", identifier, str(new)],
            capture_output=True,
            text=True,
            check=False,
        )
        if signed.returncode != 0:
            raise PalSecretsError(f"codesign failed: {signed.stderr.strip()}")
        _check_private_python(new, identifier)
        record = {"source": str(source), "link": link_text, "identifier": identifier, "links": links}
        (venv / PRIVATE_PYTHON_RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(new, python)
    except BaseException:
        if python.is_symlink():
            # bin/python is still the link, so the record is wrong.
            (venv / PRIVATE_PYTHON_RECORD).unlink(missing_ok=True)
        new.unlink(missing_ok=True)
        for link in created:
            link.unlink(missing_ok=True)
        raise
    print(f"PAL's venv now runs a private Python copy ({identifier}), made from {source}.")
    return python


def _check_private_python(python: Path, identifier: str) -> None:
    """Start the copy and check that the process really has the copy's signature.

    Some builds (for example a framework Python) start another binary, so the copy would not decide the cdhash.
    """

    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); "
        "from utils.op_keychain_cache import backend; i = backend().own_identity(); "
        "print(json.dumps([i.identifier, i.adhoc, i.team_id]))"
    )
    result = subprocess.run(
        [str(python), "-c", code, str(PROJECT_ROOT)], capture_output=True, text=True, timeout=60, check=False
    )
    try:
        got = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise PalSecretsError(f"the private Python copy does not start: {result.stderr.strip()[-300:]}") from None
    if got != [identifier, True, None]:
        raise PalSecretsError(
            "this Python build runs another binary, so a private copy cannot give PAL its own cdhash; use a "
            "uv or python-build-standalone Python for PAL's venv"
        )


def restore_python_link(venv: Path) -> bool:
    """Undo install_private_python. Return True when there was a private copy."""

    with _exclusive_lock(timeout=LOCK_WAIT_SECONDS) as locked:
        if not locked:
            raise PalSecretsError("another pal-secrets command holds the lock; try again")
        return _restore_python_link(venv)


def _restore_python_link(venv: Path) -> bool:
    record_path = venv / PRIVATE_PYTHON_RECORD
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    python = venv / "bin" / "python"
    target = record.get("link") or record["source"]
    if not python.is_symlink() and signature_identifier(python) == record["identifier"]:
        temp_link = python.with_name(".python.pal-secrets-link")
        temp_link.unlink(missing_ok=True)
        temp_link.symlink_to(target)
        os.replace(temp_link, python)
    for link in record.get("links", []):
        if Path(link).is_symlink():
            Path(link).unlink()
    record_path.unlink()
    print(f"Restored {python} as a link to {target}.")
    return True


def _run_as_private_python(argv: list[str]) -> cache.Identity:
    """Return the identity of this process. If it is not the private copy, repair the copy and restart in it."""

    identity = cache.backend().own_identity()
    if identity.problem() is None:
        return identity
    if os.environ.get(_REEXEC_MARK):
        raise PalSecretsError(f"the private Python copy does not work: {identity.problem()}")
    python = install_private_python(venv_dir())
    print("Restarting with the private Python copy.", flush=True)
    command = [str(SCRIPT_PATH)] if SCRIPT_PATH.is_file() else ["-m", "utils.pal_secrets"]
    os.execve(str(python), [str(python), *command, *argv], {**os.environ, _REEXEC_MARK: "1"})
    raise AssertionError("unreachable")  # pragma: no cover


# --- Cache changes ----------------------------------------------------------------------------------------------


@contextlib.contextmanager
def _exclusive_lock(timeout: float | None) -> Iterator[bool]:
    """Let only one pal-secrets command change the cache or the Python copy at a time.

    ``timeout=None`` waits as long as necessary, and ``0`` does not wait. Yield False when the lock stays busy.
    The lock belongs to an open file, so one process must never take it twice at the same time.
    """

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout is None else time.monotonic() + timeout
    with open(LOCK_PATH, "a") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX if deadline is None else fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert deadline is not None  # a blocking flock does not raise BlockingIOError
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.2)
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _format_time(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(epoch))


def _created_at(path: Path) -> float:
    status = path.stat()
    # st_birthtime exists on macOS. Elsewhere the modification time stands in; it is later, so a file can stay
    # longer, but expire also checks the state file.
    return getattr(status, "st_birthtime", status.st_mtime)


def _delete_caches(keychain, doomed: list[tuple[str, Path, str]]) -> None:
    """Delete each ``(cdhash, path, reason)``: first the state file, then, after a grace time, the keychain."""

    if not doomed:
        return
    unlocked = False
    for cdhash, path, _ in doomed:
        cache.remove_state(cdhash)
        unlocked = unlocked or keychain.state(path) == cache.UNLOCKED
    if unlocked:
        time.sleep(DELETE_GRACE_SECONDS)
    for _, path, reason in doomed:
        if keychain.delete(path):
            print(f"Deleted {path.name} ({reason})")


def _write_cache(keychain, cdhash: str, values: dict[tuple[str, str], str], ttl: int) -> float:
    """Replace every cache with a new keychain for ``cdhash``. ``values`` is keyed by (account, reference)."""

    _delete_caches(keychain, [(other, path, "replaced") for other, path in cache.all_keychain_paths()])
    path = cache.keychain_path(cdhash)
    now = time.time()
    expires_at = now + ttl
    try:
        keychain.create(path, secrets.token_urlsafe(32).encode("ascii"), ttl + cache.LOCK_AFTER_TTL_SECONDS)
        for (account, reference), value in values.items():
            item = cache.encode_item(account, reference, value, expires_at)
            keychain.add(path, cache.item_account(account, reference), item, f"PAL cache: {account} {reference}")
        # Read one item back before the state file says that the cache is complete.
        (account, reference), value = next(iter(values.items()))
        data = keychain.find(path, cache.item_account(account, reference))
        if data is None or cache.decode_item(data, account, reference)[0] != value:
            raise PalSecretsError("an item that refresh wrote does not read back")
        cache.write_state(cache.CacheState(cdhash, now, expires_at, tuple(sorted(values))))
    except BaseException:
        cache.remove_state(cdhash)
        keychain.delete(path)
        raise
    return expires_at


# --- Commands ---------------------------------------------------------------------------------------------------


def command_refresh(args: argparse.Namespace) -> int:
    _require_runtime_venv()
    identity = _run_as_private_python(args.argv)
    keychain = cache.backend()
    with _exclusive_lock(timeout=LOCK_WAIT_SECONDS) as locked:
        if not locked:
            raise PalSecretsError(f"another pal-secrets command held the lock for {LOCK_WAIT_SECONDS}s")
        references = _accounted_references(configuration_sources())
        if not references:
            raise PalSecretsError(
                "found no op:// references in .env, ~/.claude.json, the Claude Desktop config or "
                "PAL_OP_CACHE_REFERENCES"
            )
        if args.if_needed:
            state = cache.read_state(identity.cdhash)
            path = cache.keychain_path(identity.cdhash)
            if (
                state is not None
                and set(references) <= set(state.entries)
                and state.expires_at - time.time() > IF_NEEDED_SECONDS
                and keychain.state(path) == cache.UNLOCKED
            ):
                print(f"The cache is valid until {_format_time(state.expires_at)}; no refresh needed.")
                return 0
        by_account: dict[str, list[str]] = {}
        for account, reference in references:
            by_account.setdefault(account, []).append(reference)
        op_path = find_op()
        values: dict[tuple[str, str], str] = {}
        # All or nothing over every account: if one read fails, the old cache stays until its own expiry.
        for account, account_references in sorted(by_account.items()):
            print(
                f"Reading {len(account_references)} reference(s) of {account} from 1Password. "
                "Approve the 1Password prompt."
            )
            for reference, value in read_secrets(account_references, op_path, account, args.timeout).items():
                values[(account, reference)] = value
        expires_at = _write_cache(keychain, identity.cdhash, values, args.ttl_minutes * 60)
    print(f"Cached {len(values)} reference(s) until {_format_time(expires_at)}.")
    return 0


def command_expire(args: argparse.Namespace) -> int:
    keychain = cache.backend()
    with _exclusive_lock(timeout=0) as locked:
        if not locked:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pal-secrets expire: a refresh runs now; skipped.")
            return 0
        now = time.time()
        doomed = []
        for cdhash, path in cache.all_keychain_paths():
            # Decide from the lock state, the file age and the state file only: expire never reads an item.
            state = cache.read_state(cdhash)
            if keychain.state(path) != cache.UNLOCKED:
                reason = "locked"
            elif _created_at(path) + cache.MAX_TTL_SECONDS <= now:
                reason = "older than 24 hours"
            elif state is None:
                reason = "no state file"
            elif now >= state.expires_at:
                reason = "expired"
            else:
                continue
            doomed.append((cdhash, path, reason))
        if doomed:
            # expire runs every 15 minutes, so it writes to the log only when it deletes something.
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pal-secrets expire", flush=True)
        _delete_caches(keychain, doomed)
        _remove_orphan_states()
    return 0


def _remove_orphan_states() -> None:
    live = {cdhash for cdhash, _ in cache.all_keychain_paths()}
    for state_file in cache.STATE_DIR.glob("*.json"):
        if state_file.stem not in live:
            state_file.unlink(missing_ok=True)


def command_clear(args: argparse.Namespace) -> int:
    keychain = cache.backend()
    with _exclusive_lock(timeout=None):
        doomed = [(cdhash, path, "cleared") for cdhash, path in cache.all_keychain_paths()]
        _delete_caches(keychain, doomed)
        _remove_orphan_states()
    if not doomed:
        print("There is no cache to delete.")
    return 0


def command_status(args: argparse.Namespace) -> int:
    sources = configuration_sources()
    print(f"PAL_OP_CACHE: {_first_setting(sources, 'PAL_OP_CACHE') or 'off (not set)'}")
    print(f"PAL_OP_ACCOUNT: {_first_setting(sources, 'PAL_OP_ACCOUNT') or 'not set'}")
    for label in (REFRESH_LABEL, EXPIRE_LABEL):
        print(f"LaunchAgent {label}: {'loaded' if _agent_loaded(label) else 'not loaded'}")

    keychain = cache.backend()
    identity = keychain.own_identity()
    problem = identity.problem()
    print(f"Python: {sys.executable} ({identity.identifier}, cdhash {identity.cdhash[:12]}...)")
    print(f"  {'private copy: PAL can use the cache' if problem is None else 'cannot use the cache: ' + problem}")

    own_state = cache.read_state(identity.cdhash)
    caches = cache.all_keychain_paths()
    for cdhash, path in caches:
        state = cache.read_state(cdhash)
        mine = " (this Python)" if cdhash == identity.cdhash else " (another Python; PAL cannot use it)"
        expiry = f", expires {_format_time(state.expires_at)}" if state else ", no state file"
        print(f"Cache {path.name}{mine}: {keychain.state(path)}{expiry}")
    if not caches:
        print("Cache: none")

    cached = set(own_state.entries) if own_state else set()
    references, missing = collect_references(sources)
    print(f"References ({len(references)}):")
    for (account, reference), names in references.items():
        mark = "cached" if (account, reference) in cached else "not cached"
        print(f"  [{mark}] {account} {reference}  ({', '.join(names)})")
    for name in missing:
        print(f"  [no account] {name}: set PAL_OP_ACCOUNT or {_ACCOUNT_PREFIX}<variable>")
    return 0


def command_init(args: argparse.Namespace) -> int:
    venv = _require_runtime_venv()
    sources = configuration_sources()
    if not _accounted_references(sources) and not _first_setting(sources, "PAL_OP_ACCOUNT"):
        raise PalSecretsError("set PAL_OP_ACCOUNT, and set the op:// references, before init")
    try:
        op_path = find_op()
    except PalSecretsError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        op_path = None
    python = install_private_python(venv)

    hour, minute = args.refresh_at
    search_path = ([str(Path(op_path).parent)] if op_path else []) + _OP_SEARCH_PATH.split(":")
    path_value = ":".join(dict.fromkeys(search_path))
    refresh = _agent_plist(python, REFRESH_LABEL, ["refresh", "--if-needed", "--notify"], path_value)
    refresh["StartCalendarInterval"] = {"Hour": hour, "Minute": minute}
    # Also at login: after a restart the keychain is locked, and only a refresh makes a new one.
    refresh["RunAtLoad"] = True
    expire = _agent_plist(python, EXPIRE_LABEL, ["expire"], path_value)
    expire["StartInterval"] = EXPIRE_INTERVAL_SECONDS
    expire["RunAtLoad"] = True
    # Only expire is a background job; refresh starts the 1Password prompt.
    expire["ProcessType"] = "Background"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    for label, plist in ((EXPIRE_LABEL, expire), (REFRESH_LABEL, refresh)):
        _install_agent(label, plist)
        print(f"Installed LaunchAgent {label}")

    print(f"\nrefresh runs every day at {hour:02d}:{minute:02d} and at login; expire runs every 15 minutes.")
    print(f"launchd started the first refresh now: approve the 1Password prompt. Log: {LOG_PATH}")
    print("Next steps:")
    print("  1. Set PAL_OP_CACHE=keychain in .env or in the env block of the pal MCP server.")
    print("  2. Restart PAL, so that it runs on the private Python copy (in Claude Code: /mcp, pal, Reconnect).")
    print(f"  3. Check the cache: {python} {SCRIPT_PATH} status")
    return 0


def command_uninstall(args: argparse.Namespace) -> int:
    for label in (REFRESH_LABEL, EXPIRE_LABEL):
        _launchctl("bootout", f"gui/{os.getuid()}/{label}")
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if plist_path.exists():
            plist_path.unlink()
            print(f"Removed LaunchAgent {label}")
    command_clear(args)
    try:
        restore_python_link(runtime_venv())
    except PalSecretsError as exc:
        print(f"warning: the venv's Python link was not restored: {exc}", file=sys.stderr)
        return 1
    return 0


# --- launchd ----------------------------------------------------------------------------------------------------


def _agent_plist(python: Path, label: str, arguments: list[str], path_value: str) -> dict:
    return {
        "Label": label,
        "ProgramArguments": [str(python), str(SCRIPT_PATH), *arguments],
        "WorkingDirectory": str(PROJECT_ROOT),
        "EnvironmentVariables": {"PATH": path_value},
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }


def _install_agent(label: str, plist: dict) -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    _launchctl("bootout", f"gui/{os.getuid()}/{label}")
    with open(plist_path, "wb") as handle:
        plistlib.dump(plist, handle)
    result = _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path))
    if result.returncode != 0:
        raise PalSecretsError(f"launchctl bootstrap {label} failed: {result.stderr.strip()}")


def _agent_loaded(label: str) -> bool:
    return _launchctl("print", f"gui/{os.getuid()}/{label}").returncode == 0


def _launchctl(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *arguments], capture_output=True, text=True, check=False)


def _notify(message: str) -> None:
    """Show a macOS notification. The message goes to osascript as an argument, never as script text."""

    script = ["-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "PAL secrets"']
    subprocess.run(["osascript", *script, "-e", "end run", message], capture_output=True, check=False)


# --- Entry point ------------------------------------------------------------------------------------------------


def _refresh_time(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if not match:
        raise argparse.ArgumentTypeError("use HH:MM, for example 08:30")
    return int(match.group(1)), int(match.group(2))


def _ttl_minutes(text: str) -> int:
    value = int(text)
    if not 1 <= value <= cache.MAX_TTL_SECONDS // 60:
        raise argparse.ArgumentTypeError(f"use 1 to {cache.MAX_TTL_SECONDS // 60}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pal-secrets", description=(__doc__ or "").split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="make the private Python copy and install the LaunchAgents")
    init.add_argument("--refresh-at", type=_refresh_time, default=(8, 30), metavar="HH:MM", help="default 08:30")
    init.set_defaults(handler=command_init)
    refresh = commands.add_parser("refresh", help="read the references from 1Password and write a new cache")
    refresh.add_argument("--ttl-minutes", type=_ttl_minutes, default=cache.MAX_TTL_SECONDS // 60)
    refresh.add_argument("--timeout", type=int, default=_DEFAULT_OP_TIMEOUT_SECONDS, help="seconds to wait for op")
    refresh.add_argument("--if-needed", action="store_true", help="skip if the cache stays valid for 12 more hours")
    refresh.add_argument("--notify", action="store_true", help="show a notification on failure (for launchd)")
    refresh.set_defaults(handler=command_refresh)
    for name, handler, text in (
        ("status", command_status, "show the state of the cache"),
        ("expire", command_expire, "delete the cache if it has expired or locked"),
        ("clear", command_clear, "delete the cache now"),
        ("uninstall", command_uninstall, "remove the LaunchAgents and the cache, and restore the Python link"),
    ):
        commands.add_parser(name, help=text).set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    args.argv = argv
    # Keep stdout and stderr lines in order in a terminal, a pipe and the launchd log.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError):
                reconfigure(line_buffering=True)
    if sys.platform != "darwin":
        print("pal-secrets: the keychain cache needs macOS", file=sys.stderr)
        return 2
    if args.command != "expire":
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pal-secrets {args.command}", flush=True)
    try:
        return args.handler(args)
    except (PalSecretsError, cache.KeychainCacheError) as exc:
        print(f"pal-secrets {args.command}: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - a launchd job must still notify
        # Only the type and the place: the text of an unexpected error could hold data.
        trace = exc.__traceback__
        while trace is not None and trace.tb_next is not None:
            trace = trace.tb_next
        where = f" at {Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}" if trace else ""
        print(f"pal-secrets {args.command}: unexpected {type(exc).__name__}{where}", file=sys.stderr)
    if getattr(args, "notify", False):
        # A fixed text: the error, which can name references, stays in the log.
        _notify(f"The refresh failed. See {LOG_PATH}, then run pal-secrets refresh in Terminal.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
