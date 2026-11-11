"""Optional macOS Keychain cache for 1Password secret references (PAL_OP_CACHE=keychain).

``pal-secrets refresh`` (utils/pal_secrets.py) is the only writer. It deletes the old cache keychain, creates a
new one with a random password that it never stores, adds one item per ``op://`` reference, and then writes a
state file that holds no secret. The new keychain is unlocked. Once it locks (inactivity timeout, restart,
``expire``), nobody can unlock it again, and the next ``refresh`` replaces it.

PAL only reads. Three macOS rules decide how:
- A read of a locked keychain always shows an unlock dialog (no Security API flag stops it). So PAL reads the
  state file first, checks the lock state with ``SecKeychainGetStatus``, and reads only an unlocked keychain.
- Each item records the code hash (cdhash) of the binary that wrote it, and a binary with another cdhash gets a
  dialog. So the file name holds the cdhash of the writer, and a reader opens only the file for its own cdhash.
- Every script that runs on the same binary has the same cdhash. So ``pal-secrets`` gives PAL's venv its own
  re-signed copy of the Python binary, and the cache works only in a process that runs that private copy.

This module never logs or prints a secret value.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from ctypes import POINTER, byref, c_char_p, c_int32, c_int64, c_long, c_uint8, c_uint32, c_void_p
from dataclasses import dataclass
from pathlib import Path

KEYCHAIN_DIR = Path.home() / "Library" / "Keychains"
STATE_DIR = Path.home() / "Library" / "Application Support" / "pal-secrets"
SERVICE = "pal-op-cache"
SCHEMA_VERSION = 1
MAX_TTL_SECONDS = 24 * 60 * 60
# The keychain locks itself this long after the TTL, so the inactivity timer never fires while PAL still reads.
LOCK_AFTER_TTL_SECONDS = 2 * 60 * 60
# PAL stops reading this long before the expiry (at most a tenth of the TTL), so that expire and refresh never
# delete the keychain under a running read.
READ_MARGIN_SECONDS = 5 * 60
# The signing identifier of the private Python copy starts with this text.
PRIVATE_IDENTIFIER_PREFIX = "org.pal-mcp-server.python."

_KEYCHAIN_NAME = re.compile(r"pal-secrets-([0-9a-f]{40})\.keychain-db")

# Keychain states, from SecKeychainGetStatus.
UNLOCKED = "unlocked"
LOCKED = "locked"
ABSENT = "absent"

# Lookup results. Every result other than HIT makes PAL fall back to `op read`.
HIT = "hit"
MISSING = "missing"
EXPIRED = "expired"


class KeychainCacheError(Exception):
    """The cache cannot work in this process, or a cache item is not in the expected format."""


# --- Names --------------------------------------------------------------------------------------------------


def keychain_path(cdhash: str, directory: Path | None = None) -> Path:
    """Return the cache keychain for the binary with ``cdhash``."""

    return (directory or KEYCHAIN_DIR) / f"pal-secrets-{cdhash}.keychain-db"


def all_keychain_paths(directory: Path | None = None) -> list[tuple[str, Path]]:
    """Return ``(cdhash, path)`` for every cache keychain file. Symbolic links and other names are ignored."""

    found = []
    for path in sorted((directory or KEYCHAIN_DIR).glob("pal-secrets-*.keychain-db")):
        match = _KEYCHAIN_NAME.fullmatch(path.name)
        if match and not path.is_symlink() and path.is_file():
            found.append((match.group(1), path))
    return found


def item_account(account: str, reference: str) -> str:
    """Return the item name for ``reference`` read with the 1Password ``account``.

    Both go into the hash with their lengths, so that no other pair gives the same name.
    """

    digest = hashlib.sha256()
    for part in (SERVICE, str(SCHEMA_VERSION), account, reference):
        data = part.encode("utf-8")
        digest.update(len(data).to_bytes(4, "big") + data)
    return digest.hexdigest()


# --- Items ----------------------------------------------------------------------------------------------------


def encode_item(account: str, reference: str, value: str, expires_at: float) -> bytes:
    item = {"v": SCHEMA_VERSION, "account": account, "reference": reference, "value": value, "expires_at": expires_at}
    return json.dumps(item).encode("utf-8")


def decode_item(data: bytes, account: str, reference: str) -> tuple[str, float]:
    """Return ``(value, expires_at)``. The item must be for this account and reference."""

    try:
        item = json.loads(data.decode("utf-8"))
        version, value, expires_at = item["v"], item["value"], float(item["expires_at"])
        owner = (item["account"], item["reference"])
    except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
        # The exception text could hold part of the data, so do not include it.
        raise KeychainCacheError(f"a cache item is not in the expected format ({type(exc).__name__})") from None
    if version != SCHEMA_VERSION or not isinstance(value, str):
        raise KeychainCacheError("a cache item is not in the expected format (version or value)")
    if owner != (account, reference):
        raise KeychainCacheError("a cache item belongs to another account or reference")
    return value, expires_at


# --- State file -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheState:
    """What ``refresh`` wrote. Holds no secret. ``refresh`` writes it last, so it marks a complete cache."""

    cdhash: str
    created_at: float
    expires_at: float
    # Each (account, reference) pair that the cache holds.
    entries: tuple[tuple[str, str], ...]


def state_path(cdhash: str, directory: Path | None = None) -> Path:
    return (directory or STATE_DIR) / f"{cdhash}.json"


def read_state(cdhash: str) -> CacheState | None:
    """Return the state for ``cdhash``, or None when the file is missing or not valid."""

    try:
        item = json.loads(state_path(cdhash).read_text(encoding="utf-8"))
        state = CacheState(
            cdhash=str(item["cdhash"]),
            created_at=float(item["created_at"]),
            expires_at=float(item["expires_at"]),
            entries=tuple((str(account), str(reference)) for account, reference in item["entries"]),
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if item.get("v") != SCHEMA_VERSION or state.cdhash != cdhash:
        return None
    return state


def write_state(state: CacheState) -> None:
    """Write the state file in one step: a reader sees the old file or the new file, never a part."""

    path = state_path(state.cdhash)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {"v": SCHEMA_VERSION, **state.__dict__, "entries": [list(entry) for entry in state.entries]}
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def remove_state(cdhash: str) -> bool:
    """Delete the state file, so that PAL stops reading the keychain. Return True when a file was there."""

    try:
        state_path(cdhash).unlink()
        return True
    except FileNotFoundError:
        return False


# --- Identity -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """The code signature of a process."""

    cdhash: str
    identifier: str
    adhoc: bool
    team_id: str | None

    def problem(self) -> str | None:
        """Return why the cache cannot work in this process, or None when it can."""

        if self.team_id or not self.adhoc:
            # A team or Apple signature gives a partition that many other binaries share.
            return f"this Python has a team signature ({self.team_id or 'not ad-hoc'}), not an ad-hoc one"
        if not self.identifier.startswith(PRIVATE_IDENTIFIER_PREFIX):
            return "this Python is not the private copy (run `pal-secrets refresh` to make it)"
        return None


def private_identity() -> Identity:
    """Return the identity of this process, or raise KeychainCacheError when it cannot use the cache."""

    identity = backend().own_identity()
    problem = identity.problem()
    if problem:
        raise KeychainCacheError(f"the keychain cache needs PAL's private Python copy: {problem}")
    return identity


# --- Read path ------------------------------------------------------------------------------------------------


def lookup(account: str, reference: str, now: float | None = None) -> tuple[str | None, str]:
    """Read the cached value of ``reference`` for ``account`` without any dialog.

    Returns ``(value, HIT)``, or ``(None, LOCKED | MISSING | EXPIRED)``. Raises KeychainCacheError when this
    process cannot use the cache, or when the Security framework fails.
    """

    now = time.time() if now is None else now
    keychain = backend()
    cdhash = private_identity().cdhash
    state = read_state(cdhash)
    if state is None or (account, reference) not in state.entries:
        return None, MISSING
    # A short test TTL gets a short margin, so that its cache still gives hits.
    margin = min(READ_MARGIN_SECONDS, (state.expires_at - state.created_at) / 10)
    if now >= state.expires_at - margin:
        return None, EXPIRED
    path = keychain_path(cdhash)
    status = keychain.state(path)
    if status != UNLOCKED:
        return None, LOCKED if status == LOCKED else MISSING
    data = keychain.find(path, item_account(account, reference))
    if data is None:
        return None, MISSING
    value, expires_at = decode_item(data, account, reference)
    # The item's own expiry decides. An edited state file can only make PAL stop earlier.
    if now >= expires_at:
        return None, EXPIRED
    return value, HIT


_BACKEND: _MacKeychain | None = None


def backend() -> _MacKeychain:
    """Return the Security framework binding. Tests replace this function with a fake."""

    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _MacKeychain()
    return _BACKEND


# --- Security framework binding (macOS only) -----------------------------------------------------------------

_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_NO_SUCH_KEYCHAIN = -25294
_ERR_SEC_AUTH_FAILED = -25293
_ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
# A read that meets one of these (for example a keychain that refresh just deleted) is a miss, not an error.
_MISS_STATUSES = (
    _ERR_SEC_ITEM_NOT_FOUND,
    _ERR_SEC_NO_SUCH_KEYCHAIN,
    _ERR_SEC_AUTH_FAILED,
    _ERR_SEC_INTERACTION_NOT_ALLOWED,
)
_K_SEC_UNLOCK_STATE_STATUS = 1
_K_SEC_CS_SIGNING_INFORMATION = 1 << 1
_K_SEC_CODE_SIGNATURE_ADHOC = 0x0002
_K_CF_NUMBER_SINT64_TYPE = 4
_K_CF_STRING_ENCODING_UTF8 = 0x08000100
_SEC_KEYCHAIN_SETTINGS_VERS1 = 1


class _KeychainSettings(ctypes.Structure):
    _fields_ = [
        ("version", c_uint32),
        ("lockOnSleep", c_uint8),
        ("useLockInterval", c_uint8),
        ("lockInterval", c_uint32),
    ]


class _MacKeychain:
    """Thin ctypes binding to the Security framework calls that the cache needs.

    Secret values and the keychain password go to these calls in memory, never on a command line.
    """

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise KeychainCacheError("the keychain cache needs macOS")
        try:
            self._sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            self._cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        except OSError as exc:
            raise KeychainCacheError(f"cannot load the Security framework: {exc}") from exc
        sec, cf = self._sec, self._cf

        def bind(lib, name, restype, *argtypes):
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = argtypes
            return function

        self.SecKeychainOpen = bind(sec, "SecKeychainOpen", c_int32, c_char_p, POINTER(c_void_p))
        self.SecKeychainGetStatus = bind(sec, "SecKeychainGetStatus", c_int32, c_void_p, POINTER(c_uint32))
        self.SecKeychainCreate = bind(
            sec, "SecKeychainCreate", c_int32, c_char_p, c_uint32, c_char_p, c_uint8, c_void_p, POINTER(c_void_p)
        )
        self.SecKeychainSetSettings = bind(sec, "SecKeychainSetSettings", c_int32, c_void_p, POINTER(_KeychainSettings))
        self.SecKeychainLock = bind(sec, "SecKeychainLock", c_int32, c_void_p)
        self.SecKeychainDelete = bind(sec, "SecKeychainDelete", c_int32, c_void_p)
        self.SecItemAdd = bind(sec, "SecItemAdd", c_int32, c_void_p, POINTER(c_void_p))
        self.SecItemCopyMatching = bind(sec, "SecItemCopyMatching", c_int32, c_void_p, POINTER(c_void_p))
        self.SecCodeCopySelf = bind(sec, "SecCodeCopySelf", c_int32, c_uint32, POINTER(c_void_p))
        self.SecCodeCopyStaticCode = bind(sec, "SecCodeCopyStaticCode", c_int32, c_void_p, c_uint32, POINTER(c_void_p))
        self.SecCodeCopySigningInformation = bind(
            sec, "SecCodeCopySigningInformation", c_int32, c_void_p, c_uint32, POINTER(c_void_p)
        )
        self.SecCopyErrorMessageString = bind(sec, "SecCopyErrorMessageString", c_void_p, c_int32, c_void_p)

        self.CFRelease = bind(cf, "CFRelease", None, c_void_p)
        self.CFStringCreateWithCString = bind(cf, "CFStringCreateWithCString", c_void_p, c_void_p, c_char_p, c_uint32)
        self.CFStringGetCString = bind(cf, "CFStringGetCString", c_uint8, c_void_p, c_char_p, c_long, c_uint32)
        self.CFNumberGetValue = bind(cf, "CFNumberGetValue", c_uint8, c_void_p, c_long, c_void_p)
        self.CFDataCreate = bind(cf, "CFDataCreate", c_void_p, c_void_p, c_char_p, c_long)
        self.CFDataGetLength = bind(cf, "CFDataGetLength", c_long, c_void_p)
        self.CFDataGetBytePtr = bind(cf, "CFDataGetBytePtr", c_void_p, c_void_p)
        self.CFArrayCreate = bind(cf, "CFArrayCreate", c_void_p, c_void_p, POINTER(c_void_p), c_long, c_void_p)
        self.CFDictionaryGetValue = bind(cf, "CFDictionaryGetValue", c_void_p, c_void_p, c_void_p)
        self.CFDictionaryCreate = bind(
            cf,
            "CFDictionaryCreate",
            c_void_p,
            c_void_p,
            POINTER(c_void_p),
            POINTER(c_void_p),
            c_long,
            c_void_p,
            c_void_p,
        )

        self._dict_key_callbacks = ctypes.addressof(c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        self._dict_value_callbacks = ctypes.addressof(c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        self._array_callbacks = ctypes.addressof(c_void_p.in_dll(cf, "kCFTypeArrayCallBacks"))
        self._true = self._created(c_void_p.in_dll(cf, "kCFBooleanTrue").value, "kCFBooleanTrue")
        self._identity: Identity | None = None

    # Helpers ---------------------------------------------------------------------------------------------

    def _constant(self, name: str) -> int:
        value = c_void_p.in_dll(self._sec, name).value
        if not value:
            raise KeychainCacheError(f"the Security framework constant {name} is not set")
        return int(value)

    @staticmethod
    def _created(ref: int | None, what: str) -> int:
        """Return ``ref``, or raise: a NULL in a Core Foundation collection crashes the process."""

        if not ref:
            raise KeychainCacheError(f"{what} returned NULL")
        return int(ref)

    def _error(self, what: str, status: int) -> KeychainCacheError:
        message = ""
        text = self.SecCopyErrorMessageString(status, None)
        if text:
            message = ": " + (self._read_string(text) or "")
            self.CFRelease(text)
        return KeychainCacheError(f"{what} failed with status {status}{message}")

    def _read_string(self, ref: int) -> str | None:
        buffer = ctypes.create_string_buffer(1024)
        if not self.CFStringGetCString(ref, buffer, len(buffer), _K_CF_STRING_ENCODING_UTF8):
            return None
        return buffer.value.decode("utf-8", "replace")

    def _string(self, text: str) -> int:
        ref = self.CFStringCreateWithCString(None, text.encode("utf-8"), _K_CF_STRING_ENCODING_UTF8)
        return self._created(ref, "CFStringCreateWithCString")

    def _dictionary(self, entries: dict[str, int | None]) -> int:
        keys = (c_void_p * len(entries))(*[self._constant(name) for name in entries])
        values = (c_void_p * len(entries))(*entries.values())
        ref = self.CFDictionaryCreate(
            None, keys, values, len(entries), self._dict_key_callbacks, self._dict_value_callbacks
        )
        return self._created(ref, "CFDictionaryCreate")

    def _open(self, path: Path) -> c_void_p:
        keychain = c_void_p()
        status = self.SecKeychainOpen(str(path).encode("utf-8"), byref(keychain))
        if status != 0:
            raise self._error("SecKeychainOpen", status)
        return keychain

    def _release(self, refs) -> None:
        for ref in refs:
            if ref:
                self.CFRelease(ref)

    # Operations ------------------------------------------------------------------------------------------

    def own_identity(self) -> Identity:
        """Return the code signature of the main executable of this process. Computed once per process."""

        if self._identity is None:
            code, static, info = c_void_p(), c_void_p(), c_void_p()
            try:
                status = self.SecCodeCopySelf(0, byref(code))
                if status != 0:
                    raise self._error("SecCodeCopySelf", status)
                status = self.SecCodeCopyStaticCode(code, 0, byref(static))
                if status != 0:
                    raise self._error("SecCodeCopyStaticCode", status)
                status = self.SecCodeCopySigningInformation(static, _K_SEC_CS_SIGNING_INFORMATION, byref(info))
                if status != 0:
                    raise self._error("SecCodeCopySigningInformation", status)
                unique = self.CFDictionaryGetValue(info, self._constant("kSecCodeInfoUnique"))
                if not unique:
                    raise KeychainCacheError("the Python executable has no code signature (no cdhash)")
                identifier = self.CFDictionaryGetValue(info, self._constant("kSecCodeInfoIdentifier"))
                team = self.CFDictionaryGetValue(info, self._constant("kSecCodeInfoTeamIdentifier"))
                flags_ref = self.CFDictionaryGetValue(info, self._constant("kSecCodeInfoFlags"))
                flags = c_int64(0)
                if flags_ref:
                    self.CFNumberGetValue(flags_ref, _K_CF_NUMBER_SINT64_TYPE, byref(flags))
                self._identity = Identity(
                    cdhash=ctypes.string_at(self.CFDataGetBytePtr(unique), self.CFDataGetLength(unique)).hex(),
                    identifier=(self._read_string(identifier) if identifier else None) or "",
                    adhoc=bool(flags.value & _K_SEC_CODE_SIGNATURE_ADHOC),
                    team_id=self._read_string(team) if team else None,
                )
            finally:
                self._release((code.value, static.value, info.value))
        return self._identity

    def state(self, path: Path) -> str:
        """Return UNLOCKED, LOCKED or ABSENT. Never shows a dialog."""

        if not path.is_file():
            return ABSENT
        keychain = self._open(path)
        try:
            flags = c_uint32()
            status = self.SecKeychainGetStatus(keychain, byref(flags))
            if status == _ERR_SEC_NO_SUCH_KEYCHAIN:
                return ABSENT
            if status != 0:
                raise self._error("SecKeychainGetStatus", status)
            return UNLOCKED if flags.value & _K_SEC_UNLOCK_STATE_STATUS else LOCKED
        finally:
            self.CFRelease(keychain)

    def find(self, path: Path, account: str) -> bytes | None:
        """Return the data of the item, or None when it does not exist.

        Call only after state() returned UNLOCKED, from the binary that wrote the item. Otherwise this call
        shows a dialog.
        """

        keychain = self._open(path)
        owned = [keychain.value]
        try:
            search_list = self._created(
                self.CFArrayCreate(None, (c_void_p * 1)(keychain.value), 1, self._array_callbacks), "CFArrayCreate"
            )
            owned.append(search_list)
            service = self._string(SERVICE)
            owned.append(service)
            account_name = self._string(account)
            owned.append(account_name)
            query = self._dictionary(
                {
                    "kSecClass": self._constant("kSecClassGenericPassword"),
                    "kSecAttrService": service,
                    "kSecAttrAccount": account_name,
                    "kSecMatchSearchList": search_list,
                    "kSecMatchLimit": self._constant("kSecMatchLimitOne"),
                    "kSecReturnData": self._true,
                    "kSecUseAuthenticationUI": self._constant("kSecUseAuthenticationUIFail"),
                }
            )
            owned.append(query)
            result = c_void_p()
            status = self.SecItemCopyMatching(query, byref(result))
            if status in _MISS_STATUSES:
                return None
            if status != 0:
                raise self._error("SecItemCopyMatching", status)
            data = self._created(result.value, "SecItemCopyMatching")
            owned.append(data)
            return ctypes.string_at(self.CFDataGetBytePtr(data), self.CFDataGetLength(data))
        finally:
            self._release(owned)

    def create(self, path: Path, password: bytes, lock_after_seconds: int) -> None:
        """Create an unlocked keychain at ``path`` that locks after ``lock_after_seconds`` of inactivity."""

        keychain = c_void_p()
        status = self.SecKeychainCreate(str(path).encode("utf-8"), len(password), password, 0, None, byref(keychain))
        if status != 0:
            raise self._error("SecKeychainCreate", status)
        try:
            settings = _KeychainSettings(_SEC_KEYCHAIN_SETTINGS_VERS1, 0, 1, lock_after_seconds)
            status = self.SecKeychainSetSettings(keychain, byref(settings))
            if status != 0:
                error = self._error("SecKeychainSetSettings", status)
                self.SecKeychainDelete(keychain)
                raise error
        finally:
            self.CFRelease(keychain)

    def add(self, path: Path, account: str, data: bytes, label: str) -> None:
        """Add a generic password item with the default access: only this binary reads it without a dialog."""

        keychain = self._open(path)
        owned = [keychain.value]
        try:
            for text in (SERVICE, account, label):
                owned.append(self._string(text))
            service, account_name, item_label = owned[1:4]
            value = self._created(self.CFDataCreate(None, data, len(data)), "CFDataCreate")
            owned.append(value)
            query = self._dictionary(
                {
                    "kSecClass": self._constant("kSecClassGenericPassword"),
                    "kSecAttrService": service,
                    "kSecAttrAccount": account_name,
                    "kSecAttrLabel": item_label,
                    "kSecValueData": value,
                    "kSecUseKeychain": keychain.value,
                }
            )
            owned.append(query)
            status = self.SecItemAdd(query, None)
            if status != 0:
                raise self._error("SecItemAdd", status)
        finally:
            self._release(owned)

    def lock(self, path: Path) -> None:
        if not path.is_file():
            return
        keychain = self._open(path)
        try:
            status = self.SecKeychainLock(keychain)
            if status not in (0, _ERR_SEC_NO_SUCH_KEYCHAIN):
                raise self._error("SecKeychainLock", status)
        finally:
            self.CFRelease(keychain)

    def delete(self, path: Path) -> bool:
        """Lock and delete the keychain file. Needs no password. Return False when there was no file."""

        if path.is_symlink() or not path.is_file():
            return False
        self.lock(path)
        keychain = self._open(path)
        try:
            status = self.SecKeychainDelete(keychain)
            if status == _ERR_SEC_NO_SUCH_KEYCHAIN:
                return False
            if status != 0:
                raise self._error("SecKeychainDelete", status)
            return True
        finally:
            self.CFRelease(keychain)
