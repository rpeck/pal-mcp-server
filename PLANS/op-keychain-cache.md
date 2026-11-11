# Plan: a 24-hour Keychain cache for 1Password secrets

**Status:** implemented on branch `feat/op-keychain-cache` (2026-11-08), not committed. The design changed after the spike and the council review: **§9 replaces §4.2–§4.6 where they differ.** **Owner:** Raymond. **Runs on:** macOS only.

Read this whole file before you start. It is self-contained: the session that wrote it is not available to you.

## 1. Problem

PAL can read an API key from 1Password: when a variable holds an `op://` secret reference, `utils/env.py`
(`get_env` → `_read_op_secret`) runs `op read` and keeps the value for the life of the PAL process.

Each Claude Code session starts its own PAL process over stdio. So each new session runs `op read` again, and
1Password asks for Touch ID again. Raymond must be at the laptop whenever a session starts.

**Goal:** one Touch ID in the morning covers all PAL processes for at most 24 hours. The cache must not
outlive those 24 hours, and every failure must fall back to today's behavior (`op read` with Touch ID).

## 2. Decision

Cache the secret values in a **dedicated macOS keychain** that locks itself, refreshed once a day.

**The cache is optional and off by default** (Raymond, 2026-11-07). PAL has other users. For them, and for
any machine where nobody turns it on, PAL behaves exactly as it does today: no keychain, no launchd job, no
new log lines. The cache trades some protection for convenience (§3), so each user must choose it.

Rejected options (2026-11-07):
- **A 1Password service-account token:** a standing credential on disk, valid until revoked, that reads a whole
  vault, now and later. Better audit and revocation, but a larger blast radius and no expiry.
- **Local Redis:** plain text behind a local port, no password by default, may persist to disk.
- **One shared PAL server over HTTP:** no cache at all, but the largest change. All sessions would share one
  conversation memory, and workflow tools would run one call at a time across all sessions.

## 3. Threat model

| | Protected | Not protected |
|---|---|---|
| Lost or stolen laptop (disk) | Yes: keychains are encrypted at rest | |
| The cache after its 24 hours | Yes: PAL stops reading at the TTL; `expire` deletes the file within 15 minutes while the Mac is awake. A deleted file is cryptographically inaccessible, not erased (APFS, SSD). | |
| Malware that runs as Raymond during the 24 hours | | No: while the cache is unlocked, any code that runs PAL's Python (the private copy, §9) can read the cache and probably replace items in it |
| A key that leaked during the 24 hours | | No: it stays valid until rotated at the provider |

Keep the cached keys narrow: restrict each key to its API, and set a spending limit where the provider allows.

## 4. Design

### 4.1 Opt-in, off by default

- New variable `PAL_OP_CACHE`: unset or `off` (default) keeps today's behavior exactly. `keychain` turns the
  cache on. Any other value logs one warning and acts as `off`.
- On a platform other than macOS, `keychain` logs one warning and acts as `off`.
- Turning it on takes two deliberate steps: set `PAL_OP_CACHE=keychain`, and run `pal-secrets init`. Without
  `init` there is no keychain, so the setting alone only makes PAL fall back to `op read`.
- Nothing installs the LaunchAgents or creates the keychain on its own: not `run-server.sh`, not PAL at
  startup, not a package install. Only `pal-secrets init` does.
- `.env.example` lists the variable commented out, with a short note on what it trades (§3).

### 4.2 The cache keychain

- A dedicated keychain, for example `~/Library/Keychains/pal-secrets.keychain-db`. Never the login keychain:
  the login keychain stays unlocked for the whole login session.
- It has its own random password. That password lives in 1Password (an item that Raymond creates or `init`
  creates), never on disk in plain text and never in the login keychain.
- Lock settings: lock after a timeout (`security set-keychain-settings -t <seconds>`), and decide in the spike
  (§6) whether to add lock-on-sleep (`-l`).
- **Defense in depth, three layers**, because the macOS timeout may count only inactivity (§6, question 1):
  1. Each item carries its expiry time; PAL treats an expired item as missing.
  2. A launchd job locks the keychain and deletes the items when the expiry passes (§4.5).
  3. The keychain's own timeout.

### 4.3 The `pal-secrets` command

A script in the repo, for example `scripts/pal_secrets.py`, also exposed as a `[project.scripts]` entry
`pal-secrets`. Subcommands:

| Command | What it does |
|---|---|
| `init` | Create the keychain, set its lock settings, and check that the 1Password item for its password exists. Safe to run again. |
| `refresh` | In one process: read the keychain password and every cached reference with `op read` (one Touch ID covers all of them), unlock the keychain, write each value with an expiry 24 hours ahead, then lock the keychain's write access as the spike decides. Overwrites old items. |
| `status` | Locked or unlocked, the expiry time, and which references are cached. Never prints a value. |
| `expire` | If the expiry has passed: delete the items and lock the keychain. Run hourly by launchd. |
| `clear` | Delete the items and lock the keychain now. |

**Which references to cache:** the `op://` values that PAL would read. `refresh` runs from launchd, outside
PAL's environment, so it must collect them itself:
- PAL's `.env` in the repo root;
- the `env` block of the `pal` server in `~/.claude.json` (Claude Code passes it to PAL; on the work laptop
  `GEMINI_API_KEY` is set there);
- an optional extra list, for example `PAL_OP_CACHE_REFERENCES` in `.env`.

Store items under a name derived from the reference (for example a hash), so that the cache key is the
reference, not the variable name.

### 4.4 PAL's read path

In `utils/env.py`, before `op read`, when `PAL_OP_CACHE=keychain`:
1. If the keychain is locked, skip the cache. Do not trigger any dialog (§6, question 2).
2. Read the item for the reference. If it is missing or past its expiry, skip the cache.
3. Otherwise return the cached value. It goes into `_OP_SECRETS` like a value from `op read`.
4. In every skip case, fall back to `op read`, which may ask for Touch ID.

PAL never writes the cache. `refresh` is the only writer, so the 24-hour window starts only with Raymond's
Touch ID.

Log, once per reference and process, at INFO: "cached" or "cache locked or expired, reading from 1Password".
Never log a value.

### 4.5 The morning prompt (launchd)

Two LaunchAgents in `~/Library/LaunchAgents/`, installed by `init` or by a documented command:
- **Refresh, every morning** (for example 08:30, `StartCalendarInterval`): runs `pal-secrets refresh`. The
  1Password app shows its Touch ID prompt, and that prompt is the reminder. If the laptop sleeps at 08:30,
  launchd runs the missed job on wake. On failure, show a macOS notification that says how to retry.
- **Expire, every hour:** runs `pal-secrets expire`.

### 4.6 Writing secrets safely

- Never pass a secret value or the keychain password on a command line: `security add-generic-password -w
  <value>` and `security unlock-keychain -p <password>` put them in the process list. Use the Security
  framework instead, for example through the `keyring` package (its macOS backend can target a given keychain;
  confirm in the spike) or `ctypes`.
- Read `op` output from a pipe, never from a file.

## 5. Rollout

Nothing changes for anyone who does not set `PAL_OP_CACHE=keychain`. What Raymond sees once it is on:

| When | What happens |
|---|---|
| First setup | `pal-secrets init`, then `pal-secrets refresh` (one Touch ID). |
| Every morning | One Touch ID prompt from 1Password, triggered by launchd. |
| A new Claude Code session during the day | No prompt: PAL reads the cache. |
| The cache expired or the refresh was skipped | Today's behavior: Touch ID when PAL starts. |
| The laptop slept through 08:30 | The refresh runs on wake. |
| PAL started by Claude Desktop | The same cache works; nothing to change. |

Document it in `docs/1password.md` (a new section), `.env.example` and `CHANGELOG.md`.

## 6. Verify first (a short spike, before the code)

Answer these with throwaway scripts and write the answers into this file:

1. **Does `-t` count inactivity or absolute time?** If each read resets the timer, a busy PAL never lets the
   keychain lock; then layers 1 and 2 of §4.2 carry the expiry.
2. **What happens when a process reads a locked keychain?** It must fail quietly and never show a password
   dialog. Find the call that checks the lock state without a prompt (for example `SecKeychainGetStatus`), and
   check how `keyring` behaves.
3. **Does `keyring`'s macOS backend accept a custom keychain path**, and does it keep values out of the
   process list?
4. **Can `op read` show its Touch ID prompt when launchd starts it**, outside a terminal? If not, the morning
   job shows a notification that opens a terminal command instead.
5. **Does one `op` process cover several `op read` calls with one Touch ID?** If not, read all references in one
   call, for example with `op inject` or `op run`.

### 6.1 Spike answers (2026-11-07, macOS 15 / Darwin 24.6, on Raymond's personal Mac)

Throwaway keychains in the session scratchpad, dummy values only. This Mac has no `op`, so questions 4 and 5
stay open.

1. **Inactivity.** With `-t 20`, a read every 5 s kept the keychain unlocked for 40 s. It locked about 20 s
   after the last read. `SecKeychainGetStatus` does not reset the timer. So layers 1 and 2 of §4.2 carry the
   24-hour limit; the timeout is only a backstop.
2. **A read of a locked keychain always shows the unlock dialog.** These do not stop it:
   - `SecKeychainSetUserInteractionAllowed(False)` with `SecKeychainFindGenericPassword`: the dialog stayed
     open about 97 s, then the call returned `-128` (user canceled).
   - `SecItemCopyMatching` with `kSecUseAuthenticationUI = kSecUseAuthenticationUIFail` and
     `kSecMatchSearchList = [keychain]`: the call waited on a dialog.

   `SecKeychainGetStatus` is silent on a locked keychain (status `2`, no unlock bit) and on a missing file
   (`-25294`, errSecNoSuchKeychain). **Rule:** check the status first, and read only when the unlock bit is
   set. A lock in the milliseconds between the check and the read can still show a dialog (see §4.4).
3. **`keyring` ignores a custom keychain path** (keyring 25: "Specified keychain is ignored. See #623"). Use
   `ctypes` with the Security framework. `SecKeychainCreate`, `SecItemAdd` and `SecItemCopyMatching` take
   values in memory, so nothing reaches the process list.
   - **Item access:** with the default access, only the binary that wrote an item can read it. Another Python
     gets `-25293` without a dialog. Changing the access of an existing item (`SecKeychainItemSetAccess`)
     shows a dialog. Setting "any application" (`SecACLSetContents` with a NULL application list) on a
     `SecAccess` that goes into `SecItemAdd` as `kSecAttrAccess` shows no dialog, and other Python binaries
     then read the item silently.
   - `SecKeychainCreate` does not add the keychain to the user search list, and the new keychain is unlocked.
   - Outside `~/Library/Keychains/`, `securityd` creates an old-style keychain. Keep the cache keychain in
     `~/Library/Keychains/`.
4. **Open (needs `op` and the 1Password app).** On failure, the morning job shows a notification that tells
   Raymond to run `pal-secrets refresh` in a terminal.
5. **Open, but avoided:** `refresh` reads all references in one `op run` process.

## 7. Tests

- Unit tests with a fake cache backend; no real keychain in CI (CI runs on Linux). Cover:
  - off by default: a test that fails if the default ever changes, and that checks that PAL then never touches
    a keychain;
  - a cache hit;
  - a locked keychain, an expired item and a missing item: each falls back to `op read`;
  - a backend error: falls back to `op read` with one warning;
  - no value ever in a log line.
- Tests for `pal-secrets`: reference collection from `.env` and from a sample `~/.claude.json`, expiry writing,
  `expire` and `clear`.
- A manual checklist on macOS: init, refresh (one Touch ID), a new session without a prompt, `expire` after a
  short test expiry, the fallback prompt afterwards.
- `./code_quality_checks.sh --check` passes.

## 8. Done when

- With `PAL_OP_CACHE=keychain` and a morning refresh, new Claude Code sessions start PAL without Touch ID.
- After the expiry, the keychain is locked, its items are gone, and PAL asks for Touch ID again.
- With the variable unset, behavior and logs are exactly as before.
- No secret value appears in a log, a file outside the keychain, or a process list.

## 9. Decisions and changes (2026-11-07 and 2026-11-08)

### 9.1 Changes that came from the spike

- **No stored keychain password.** `refresh` deletes the keychain and creates a new one with a random password
  (`secrets.token_urlsafe(32)`) that it never stores. Once the keychain locks, nobody can unlock it. So there is
  no 1Password item for the password, and `init` does not create a keychain.
- **One keychain per Python binary.** New-style keychains record the writer's cdhash in a partition list, and a
  reader with another cdhash gets a dialog. The file name holds the cdhash:
  `~/Library/Keychains/pal-secrets-<cdhash>.keychain-db`. Items use the default (creator-only) access.
- **`op inject`, not `op read` per reference.** One process, template on stdin, values on stdout, random markers
  between values. Raymond chose this over the council's `op run` (which puts values in a child environment).

### 9.2 Raymond's decision gates (architecture council, 2026-11-08)

| Gate | Decision |
|---|---|
| 1. Race between the status check and the read | Sidecar state file read first, 5-minute margin (or TTL/10), lock timeout TTL + 2 h, 2-second grace before a delete. No helper subprocess. |
| 2. Account binding | `PAL_OP_ACCOUNT` is required in cache mode. The item name hashes (service, version, account, reference) with lengths. |
| 3. Signer policy | Refuse a Python with a team or Apple signature (partition `teamid:` or `apple:`). |
| 4. Interpreter scope | PAL gets its own Python copy: `init` replaces `.pal_venv/bin/python` (a symlink) with a copy re-signed ad-hoc as `org.pal-mcp-server.python.<hash of venv path>`, plus a `lib/libpython3.N.dylib` link. Transparent: every launcher of `.pal_venv/bin/python` uses it. `refresh` and `run-server.sh` repair it; `uninstall` restores the link. Framework builds are refused. No Python version pin. |
| 5. One reference fails | All-or-nothing: keep the old cache, notify with op's error, no retry. |
| 6. Refresh at login | Yes: `RunAtLoad` with `refresh --if-needed` (skip when unlocked and more than 12 h left). |
| 7. Work laptop facts | Not known yet: check macOS version, MDM or endpoint agent, and SSH use on the laptop. |
| 8. Path naming | Fixed path per cdhash. |
| 9. Expire interval | Every 15 minutes. |

### 9.3 More spike answers (2026-11-08)

- **Same path, held reference:** process A held a keychain reference while another process deleted and
  re-created the keychain. A then saw the keychain as locked, also with a fresh `SecKeychainOpen` (the framework
  caches by path per process). A new process read the new cache. Errors only, no dialog: fixed path accepted.
- **`SecKeychainDelete` on a locked keychain and `SecKeychainSetSettings`:** no dialog.
- **Signing information** (`SecCodeCopySigningInformation` with `kSecCSSigningInformation`): the private copy
  gives flags `0x2` (ad-hoc) and no team; the shared uv binary `0x20002` and identifier `-`; `/usr/bin/python3`
  gives team `59GAB85EFG`.
- **The private copy** runs with the stdlib and the venv's site-packages, and has its own cdhash.
- **Homebrew framework Python:** the copy starts `Python.app`, so the start check fails and `init` refuses it.

### 9.4 Still open

- **Tracked in issue #19** (https://github.com/rpeck/pal-mcp-server/issues/19): the manual checklist. PR #18 merged as
  `2d358d0` on 2026-11-11.

- §6 questions 4 and 5 (Touch ID from launchd; one approval for `op inject`): test on a Mac with `op`.
- The manual checklist of §7, on the work laptop, plus: an SSH session falls back to `op read`; MDM does not
  block `SecKeychainCreate`.

### 9.5 After the guardian review (2026-11-10)

- **Guardian fixes:**
  - B: the private-copy install and restore run under the lock, with a unique temp file.
  - A: `refresh` waits up to 30 s for the lock, then fails with a notification.
  - C: `uninstall` restores the link text.
  - D: no empty keychain stays after a failed create.
  - E: an unexpected error notifies with a fixed text.
  - G: NULL checks for Core Foundation.
  - F: every library link is recorded.
  - The notification text is fixed. `run-server.sh` uses `--timeout 120`. `expire` logs only when it deletes.
- **More than one 1Password account (Raymond, 2026-11-10: "our external users may use multiple"):**
  - `PAL_OP_ACCOUNT_<NAME>` sets the account for one variable, and `PAL_OP_ACCOUNT` is the default.
  - PAL's reader keeps secrets by (account, reference).
  - The state file lists (account, reference) pairs.
  - `refresh` runs one `op inject` for each account, all-or-nothing.
  - A variable without an account is not cached.
- **The Mercury 3 plan (work laptop) says:** "Raymond approves each new session with Touch ID", and `op whoami`
  reports "not signed in" while `op` works. Never use `op whoami` as a check.
- **Runtime and dev venvs (Raymond):**
  - `.pal_venv` holds `requirements.txt` only.
  - `.dev_venv` holds the dev tools too. `code_quality_checks.sh` creates it, with the Python of `.pal_venv`.
  - `run-server.sh` keeps a working uv venv.

### 9.5 Where the work stopped (2026-11-10)

Done on `feat/op-keychain-cache`, not committed, `./code_quality_checks.sh --check` passes (1211 tests):
- The guardian findings A–G and the small items are fixed, with tests.
- Per-variable accounts (`PAL_OP_ACCOUNT_<NAME>`) in `utils/env.py` and in the cache, with tests and docs.
- The venv split: `.dev_venv` for the dev tools (created by `code_quality_checks.sh`, Python version of
  `.pal_venv`); `run_integration_tests.sh` uses it; `run-server.sh` keeps a working `.pal_venv`; `.gitignore`,
  black and isort skip `.dev_venv`. On Raymond's personal Mac, `.dev_venv` exists and the dev-only packages
  were removed from `.pal_venv` (52 runtime packages plus the editable install remain). `.pal_venv/uv_created`
  was added.

Still to do:
1. Change the developer docs from `.pal_venv` to `.dev_venv`: `CLAUDE.md` (lines 13, 243, 274), `AGENTS.md`
   (lines 15, 23, 29, 30), `docs/troubleshooting.md` (lines 97, 104), `docs/contributions.md` (line 124). Keep
   `.pal_venv` where a line starts the server.
2. Add the venv split to `CHANGELOG.md`.
3. Run the guardian council again on the final code (Raymond chose "re-run after fixes").
4. Then ask Raymond about the commit and the PR (stage files one by one; not the PLANS docs, not
   `#claude_config_example.json#`).

### 9.6 Second guardian review (2026-11-11): CONDITIONAL-GO, conditions met

- **M1:** `run-server.sh` reuses `.pal_venv` and now runs `uv pip install -r requirements.txt` on it.
- **M2:** `refresh` and `init` refuse any venv that is not `PROJECT_ROOT/.pal_venv`.
- **Gate 1 (Raymond: "Mirror PAL"):** `configuration_sources` returns one view per `pal` server (env block over
  `.env`, or `.env` only with `PAL_MCP_FORCE_ENV_OVERRIDE=true`), or `.env` alone when no client config has a
  `pal` server. Accounts resolve inside each view, as in `utils/env.py`.
- **L1–L10 (Raymond: all):**
  - NULL checks.
  - `--if-needed` compares the references.
  - The `libpython` link goes through the unresolved path.
  - The record is removed on a failed install.
  - `uninstall` reports a failed restore.
  - `.dev_venv` syncs both requirements files on each run.
  - Miss statuses count as misses.
  - Only the first line of an `op` error is kept.
  - Docs.
- **Not tested live:** the `refresh` path in `.pal_venv` itself, because M2 now refuses scratch venvs, and
  turning the cache on here would change the runtime venv. The manual checklist on a Mac with `op` covers it.
