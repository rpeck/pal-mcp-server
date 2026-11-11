# 1Password Secret References

PAL can read an API key from 1Password, so the key is not in plaintext in your MCP client configuration
or in `.env`. Set the variable to a 1Password secret reference. PAL reads the secret with the
[1Password CLI](https://developer.1password.com/docs/cli/) (`op read`) the first time it needs the variable.

```json
"env": {
  "GEMINI_API_KEY": "op://my-vault/gemini-api-key/credential"
}
```

PAL calls `op` only for a value that starts with `op://`. A plain value works as before.

## Setup

1. Install the 1Password CLI, version 2 or later. `op --version` shows the version.
2. In the 1Password app, open **Settings > Developer** and turn on **Integrate with 1Password CLI**.
   `op` then signs in through the app, and the app shows a Touch ID prompt when it needs your approval.
   Without this setting, `op` is usually not signed in inside the PAL process, and the read fails.
3. Make sure that `op` is on the `PATH` of the PAL process. Claude Code gives PAL its own `PATH`, which
   is usually the `PATH` of your shell. Claude Desktop starts MCP servers with a minimal `PATH`. For
   Claude Desktop, add a `PATH` entry to the `env` block that includes the folder from `command -v op`.
4. Test the reference in a terminal. `op read` prints the secret, so send the output to `/dev/null`:

   ```bash
   op read "op://my-vault/gemini-api-key/credential" > /dev/null && echo "reference OK"
   ```

On a machine without the 1Password app, such as a server, `op` can use a 1Password service account. Set
`OP_SERVICE_ACCOUNT_TOKEN` in the environment of PAL; `op` reads it, PAL does not. That token is also a
plaintext secret, so protect it like an API key.

## Get a reference

- In the 1Password app, open the item. Open the menu of the field (for example **credential**) and select
  **Copy Secret Reference**.
- The format is `op://<vault>/<item>/[<section>/]<field>`. You can use names or IDs.
- If a name contains spaces, put the reference in double quotes in `.env`. A JSON string needs no change.

## Configure PAL

Put the reference where you put a plaintext key today.

**MCP client `env` block.** For Claude Code, add it with the CLI:

```bash
claude mcp add pal -s user -e GEMINI_API_KEY=op://my-vault/gemini-api-key/credential -- \
  /path/to/pal-mcp-server/.pal_venv/bin/python /path/to/pal-mcp-server/server.py
```

Or edit the JSON configuration (`~/.claude.json` for Claude Code, `claude_desktop_config.json` for Claude
Desktop):

```json
{
  "mcpServers": {
    "pal": {
      "command": "/path/to/pal-mcp-server/.pal_venv/bin/python",
      "args": ["/path/to/pal-mcp-server/server.py"],
      "env": {
        "GEMINI_API_KEY": "op://my-vault/gemini-api-key/credential",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
      }
    }
  }
}
```

The `PATH` entry is necessary only for Claude Desktop, and only when it does not already include the
folder of `op`.

**`.env`:**

```env
GEMINI_API_KEY=op://my-vault/gemini-api-key/credential
```

The usual rules for `.env` apply. Without `PAL_MCP_FORCE_ENV_OVERRIDE=true`, a value in the client `env`
block wins over `.env`. With it, PAL uses `.env` only.

Restart PAL after a change. In Claude Code, run `/mcp`, select `pal`, and select **Reconnect**.

| Variable | Default | Meaning |
|----------|---------|---------|
| `PAL_OP_ACCOUNT` | none | Sign-in address or account ID. PAL passes it to `op read --account`. Set it when `op` is signed in to more than one account. |
| `PAL_OP_ACCOUNT_<NAME>` | none | The account for the one variable `<NAME>`, for example `PAL_OP_ACCOUNT_OPENAI_API_KEY`. Use it when your references are in more than one account. It wins over `PAL_OP_ACCOUNT`. |
| `PAL_OP_TIMEOUT_SECONDS` | `60` | How long PAL waits for one `op read`, Touch ID prompt included. A value that is not a positive whole number gives the default. |
| `PAL_OP_CACHE` | `off` | `keychain` turns on the [keychain cache](#keychain-cache-macos) (macOS only). Each cached variable needs an account. |
| `PAL_OP_CACHE_REFERENCES` | none | More references for the keychain cache, separated by commas. |

These settings must be plain values. PAL does not read a reference in them.

Any PAL setting can hold a reference. Use references for API keys and other secrets only (see
[What gets logged](#what-gets-logged)).

## How a read runs

1. The MCP client starts PAL. The variable holds the reference text until PAL first reads it.
2. PAL reads the provider keys when it starts, before it answers the MCP client. On the first read of a
   reference, PAL runs:

   ```
   op read <reference> [--account <PAL_OP_ACCOUNT_<NAME> or PAL_OP_ACCOUNT>]
   ```

   - PAL runs `op` directly, with no shell. The reference is one argument, so it cannot add a command.
   - The stdin of `op` is `/dev/null`, because the stdin of PAL carries the MCP messages.
   - `op` gets the environment of PAL, so `op` settings such as `OP_SERVICE_ACCOUNT_TOKEN` apply.
3. If 1Password needs your approval, it shows a Touch ID prompt. PAL waits up to `PAL_OP_TIMEOUT_SECONDS`.
4. PAL removes one trailing newline from the output and keeps the secret in memory.
5. Later reads of the same reference use the kept secret. `op` runs once per account and reference for the
   life of the PAL process, also when two variables hold the same reference.
6. If you change the secret in 1Password, restart PAL.

The `clink` tool starts other CLIs, for example the Gemini CLI. These CLIs get the secret, not the
reference. If the read failed, the CLI does not get the variable.

### Startup and client timeouts

PAL answers the MCP client only after its startup reads. While the Touch ID prompt waits, the client also
waits.

- Unlock 1Password before you start the client, or approve the prompt quickly.
- If the client stops waiting before PAL is ready, increase the startup timeout of the client. For Claude
  Code, set the `MCP_TIMEOUT` environment variable (in milliseconds) before you start `claude`.

## Keychain cache (macOS)

Each MCP client session starts its own PAL process, and each PAL process runs `op read` again. So each new
session can show a Touch ID prompt. The optional keychain cache lets one Touch ID in the morning cover every
PAL process for up to 24 hours.

**The cache is off by default.** Without `PAL_OP_CACHE=keychain`, PAL does not touch a keychain, and nothing
in this section applies.

### What it trades

| | Protected | Not protected |
|---|---|---|
| A lost or stolen laptop | Yes: the keychain file is encrypted, and its password is not stored anywhere. Backups of the file cannot be decrypted. | |
| Other programs, while the cache is valid | Yes, mostly: only PAL's own Python binary can read the items without a dialog (see [The private Python copy](#the-private-python-copy)) | Code that runs with PAL's Python, for example a package in PAL's venv |
| The cache after 24 hours | Yes: PAL stops reading it, and `expire` deletes it within 15 minutes | |
| Malware that runs as you during the 24 hours | | No: it can run PAL's Python, read the cached keys, and probably replace them |
| A key that leaked during the 24 hours | | No: it stays valid until you rotate it at the provider |

Keep the cached keys narrow. Restrict each key to its API, and set a spending limit where the provider allows.
Cache API keys only, not settings such as base URLs.

### Turn it on

Do the [Setup](#setup) steps first. Then:

1. Set two variables in `.env` or in the `env` block of the `pal` server:

   ```env
   PAL_OP_CACHE=keychain
   PAL_OP_ACCOUNT=my-team.1password.com
   ```

   Each cached value belongs to one account, so a reference never gives the secret of another account. If
   your references are in more than one account, also set `PAL_OP_ACCOUNT_<NAME>` for each variable whose
   account is not the default, for example `PAL_OP_ACCOUNT_OPENAI_API_KEY=client.1password.com`. A variable
   with no account is not cached: PAL logs one warning and runs `op read` for it.
2. In the PAL folder, run `init` with PAL's own Python:

   ```bash
   .pal_venv/bin/python scripts/pal_secrets.py init
   ```

   - `init` gives PAL's venv a private Python copy (see below).
   - It installs two LaunchAgents. The refresh agent starts at once, so 1Password shows one Touch ID prompt.
   - The morning refresh runs at 08:30. To change the time, add `--refresh-at 07:45`.
3. Restart PAL, so that it runs on the private copy. In Claude Code, run `/mcp`, select `pal`, and select
   **Reconnect**.
4. Check the result:

   ```bash
   .pal_venv/bin/python scripts/pal_secrets.py status
   ```

If PAL is installed as a package, the `pal-secrets` command does the same as `scripts/pal_secrets.py`.

| When | What happens |
|---|---|
| Every morning at 08:30 | launchd runs `refresh`. 1Password shows one Touch ID prompt. If the Mac sleeps at 08:30, the job runs when it wakes. |
| At login | launchd runs `refresh` if the cache has less than 12 hours left or is locked. After a restart, this gives one prompt soon after login. |
| A new session during the day | PAL reads the cache. No prompt. |
| The cache expired, or the refresh did not run | PAL runs `op read`, as without the cache. |
| A refresh fails | The old cache stays until its own expiry. A notification shows the error and tells you to run `refresh` in Terminal. The log is `~/Library/Logs/pal-secrets.log`. |
| `./run-server.sh` rebuilt the venv | The script repairs the private copy and runs `refresh` (one prompt). |

### The private Python copy

macOS gives a keychain item to the binary that wrote it. Every script that runs on that binary can read the
item, and a different binary gets a dialog. A Python interpreter is one binary for many scripts. For example,
uv keeps one copy of each Python version for all your uv projects.

So `init` gives PAL's venv a Python binary of its own:

1. It copies the interpreter that `.pal_venv/bin/python` links to into `.pal_venv/bin/python`, in place of the
   link.
2. It signs the copy ad-hoc with the identifier `org.pal-mcp-server.python.<hash of the venv path>`. The new
   signature gives the copy its own code hash (cdhash).
3. It adds `.pal_venv/lib/libpython3.N.dylib`, a link to the library of the original interpreter, because the
   binary loads that library from a path next to itself.
4. It starts the copy and checks that the process has the new signature. Then it records the change in
   `.pal_venv/pal-secrets-python.json`.

Everything that starts `.pal_venv/bin/python` (your MCP client, `run-server.sh`, launchd) then uses the copy.
You change no configuration. The copy has the same Python version and the same packages.

- **PAL uses the cache only in the private copy.** In any other Python, PAL logs one warning and runs `op read`.
  PAL also refuses a Python signed by a team or by Apple, because many binaries share that signature.
- **A rebuild of the venv** puts the link back. PAL then runs `op read` until the next `refresh`, which makes
  the copy again. `./run-server.sh` does this for you.
- **Run `pal-secrets` with `.pal_venv/bin/python` only.** It refuses another venv, for example `.dev_venv`: there it
  would change that venv and delete PAL's cache.
- **A framework Python** (for example from Homebrew) starts another binary, so a copy cannot change its
  signature. `init` refuses it. Use a uv Python, as `run-server.sh` does.
- **The library link is not covered by the signature.** Code that can change the original interpreter's files
  can already change PAL.
- **If you remove the original Python version** (for example with `uv python uninstall`), the venv stops
  working, with or without the copy.
- `uninstall` puts the link back.

### How it works

**`refresh`** is the only writer:

1. If it does not run on the private copy, it makes the copy and starts again in it.
2. It collects the references to cache (see [Which references](#which-references)).
3. It reads them with one `op inject` process for each account, so one approval covers all the references
   of an account. With two accounts, 1Password can show two prompts.
   - The references go to `op` on stdin, and the values come back on stdout. No value is on a command line, in
     a file, or in a process environment.
   - If one reference fails, in any account, nothing changes: the old cache stays until its own expiry.
4. It deletes the old state file, waits 2 seconds for running reads, and deletes the old cache keychain.
5. It creates a new keychain, `~/Library/Keychains/pal-secrets-<cdhash>.keychain-db`, with a random password.
   - It does not store the password.
   - The new keychain is unlocked, and it is not in the keychain search list.
6. It adds one item for each reference. Each item holds the account, the reference, the value, and an expiry
   24 hours ahead.
7. It reads one item back. Then it writes the state file
   `~/Library/Application Support/pal-secrets/<cdhash>.json`. The state file holds no secret. It marks the
   cache as complete.

**PAL** only reads. For each reference, on its first use:

1. PAL checks that it runs on the private copy.
2. PAL reads the state file. If the file is missing, is for another account, or is within 5 minutes of its
   expiry, PAL runs `op read`.
3. PAL checks the lock state of the keychain. This check never shows a dialog. If the keychain is missing or
   locked, PAL runs `op read`. PAL never reads a locked keychain, because that read shows an unlock dialog.
4. PAL reads the item. If the item is missing, past its own expiry, or for another account or reference, PAL
   runs `op read`.
5. Otherwise PAL uses the cached value and keeps it in memory, as it keeps a value from `op read`.

**Four limits** end the cache:

1. Each item carries its expiry, and PAL checks it. An edited state file cannot make the cache last longer.
2. The `expire` LaunchAgent runs every 15 minutes. It deletes the cache when it has expired, when the keychain
   is locked, when the state file is missing, or when the file is more than 24 hours old. It never reads an
   item. A delete needs no password.
3. The keychain locks itself after 26 hours without a read. Each read restarts this timer, so this limit is a
   backstop only.
4. A restart of the Mac locks the keychain. Nobody can unlock it, because nobody has its password. The next
   `refresh` replaces it.

### Which references

`refresh` runs outside PAL, so it works out what each PAL process sees, with the same rules as PAL:

- For each server named `pal` in `~/.claude.json` (also the servers of each project) and in the Claude Desktop
  configuration: its `env` block, with `.env` for the variables that the block does not set.
- With `PAL_MCP_FORCE_ENV_OVERRIDE=true` in `.env`: `.env` only.
- If no client configuration has a `pal` server: `.env` only.

In each of these, `refresh` caches every `op://` value. The account of a variable is `PAL_OP_ACCOUNT_<NAME>`,
else `PAL_OP_ACCOUNT`, as in PAL. `PAL_OP_CACHE_REFERENCES` adds more references, separated by commas, with
`PAL_OP_ACCOUNT`. It can be in `.env`, in an `env` block, or in the environment of `refresh`. `refresh` lists each
variable that has no account, and caches the others. `status` shows each reference with the places that use it.

The `PAL_OP_ACCOUNT` settings, `PAL_OP_TIMEOUT_SECONDS` and the cache settings are never cached.

### Commands

| Command | What it does |
|---|---|
| `init` | Makes the private Python copy and installs the two LaunchAgents. Safe to run again. |
| `refresh` | Reads the references from 1Password and writes a new cache. `--if-needed` skips when the cache has more than 12 hours left. `--ttl-minutes` sets a shorter expiry, for a test. |
| `status` | Shows the settings, the LaunchAgents, the Python, the cache state and expiry, and which references are cached. Never shows a value. |
| `expire` | Deletes the cache if it has expired or is locked. launchd runs it every 15 minutes. |
| `clear` | Deletes the cache now. |
| `uninstall` | Removes the LaunchAgents, deletes the cache, and puts the venv's Python link back. |

### Limits of the cache

- **A lock between the check and the read** shows an unlock dialog. The state file, the 5-minute margin, and the
  2-second wait before a delete make this very unlikely. Only a manual lock (for example **Lock All** in Keychain
  Access) or a lock by device management in that moment can cause it. If it happens, click **Cancel**: PAL then
  runs `op read`.
- **A PAL process that started before a refresh** sees the new cache as locked. Values that it read before stay
  in its memory. For a value that it reads for the first time after the refresh, it runs `op read`.
- **A manual refresh late in the day** can make the next morning refresh skip, because more than 12 hours are
  left. The cache then expires in the evening. Run `refresh` again, or wait for the next morning.
- **An SSH session** does not see the keychain as unlocked. PAL in an SSH session runs `op read`.
- **The morning prompt needs the 1Password app.** If `op` cannot show its prompt from launchd, the refresh
  fails and the notification appears. Run `refresh` in Terminal.
- **One PAL folder per user.** The LaunchAgents, the state folder and the lock are the same for every PAL
  folder, and each `refresh` deletes the caches of other Python copies. Turn on the cache in one PAL folder
  only.

### Turn it off

1. Remove `PAL_OP_CACHE` or set it to `off`. Restart PAL.
2. Remove the LaunchAgents and the cache, and put the Python link back:

   ```bash
   .pal_venv/bin/python scripts/pal_secrets.py uninstall
   ```

## Troubleshooting

If `op read` fails, PAL does these steps:

1. It logs one warning in `logs/mcp_server.log`:
   `Could not read GEMINI_API_KEY from 1Password (op://...): <error text>`.
2. It treats the variable as not set. The provider shows ❌ in `listmodels`, as with a missing key.
3. It does not try that reference again until it restarts.

If no other provider is configured, PAL does not start, and it logs
`At least one API configuration is required`. A missing key has the same result.

`grep 1Password logs/mcp_server.log` shows the warnings.

| Error text in the warning | Cause | Fix |
|---------------------------|-------|-----|
| `the op command is not on PATH` | PAL cannot find `op` | Add the folder of `op` to `PATH` in the `env` block, then restart PAL |
| `op read did not finish within 60s (PAL_OP_TIMEOUT_SECONDS)` | No one approved the Touch ID prompt in time | Restart PAL and approve the prompt, or increase `PAL_OP_TIMEOUT_SECONDS` |
| An `op` error about sign-in | `op` has no session in the PAL process | Turn on the app integration (see [Setup](#setup)), unlock 1Password, restart PAL |
| An `op` error about the vault, item or field | The reference is wrong | Test it with `op read` in a terminal (see [Setup](#setup)) |
| `op read returned an empty value` | The field is empty | Fill the field, or use a different field |

If `op` reads from the wrong account, set `PAL_OP_ACCOUNT`, or `PAL_OP_ACCOUNT_<NAME>` for one variable.

Do not use `op whoami` to check the sign-in. With the app integration, it can report "not signed in" while
`op read` works.

## What gets logged

- PAL never logs the secret.
- At startup, PAL logs each API key as `[PRESENT]` or `[MISSING]`, not its value.
- The failure warning shows the variable name, the reference, and the error text from `op`.
- PAL logs some settings that are not secrets with their value, for example `CUSTOM_API_URL` and
  `GEMINI_BASE_URL`. If such a setting holds a reference, the log shows the value that `op` returned.
- With the keychain cache on, PAL logs one INFO line for each reference: `Read GEMINI_API_KEY from the keychain
  cache`, or `The keychain cache is locked for GEMINI_API_KEY; reading from 1Password` (also `missing` or
  `expired`).
- A cache problem gives one warning for each PAL process, and PAL stops using the cache until it restarts. For
  example: `Cannot read the keychain cache (the keychain cache needs PAL's private Python copy: this Python is
  not the private copy ...); reading from 1Password`.

## Limits

- The Docker image does not include `op`. Use plain values with Docker.
- PAL does not read references in the `env` block of a clink client configuration
  (`conf/cli_clients/*.json`).
- `PAL_OP_ACCOUNT`, `PAL_OP_ACCOUNT_<NAME>`, `PAL_OP_TIMEOUT_SECONDS` and `PAL_OP_CACHE` cannot be references.
