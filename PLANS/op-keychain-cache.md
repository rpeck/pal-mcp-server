# Plan: a 24-hour Keychain cache for 1Password secrets

**Status:** planned, not started (2026-11-07). **Owner:** Raymond. **Runs on:** macOS only.

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
| The cache after its 24 hours | Yes: the keychain is locked and the items are deleted | |
| Malware that runs as Raymond during the 24 hours | | No: it can read the cache, as it can read an exported variable |
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
