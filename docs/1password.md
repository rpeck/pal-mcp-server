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
| `PAL_OP_TIMEOUT_SECONDS` | `60` | How long PAL waits for one `op read`, Touch ID prompt included. A value that is not a positive whole number gives the default. |

These two settings must be plain values. PAL does not read a reference in them.

Any PAL setting can hold a reference. Use references for API keys and other secrets only (see
[What gets logged](#what-gets-logged)).

## How a read runs

1. The MCP client starts PAL. The variable holds the reference text until PAL first reads it.
2. PAL reads the provider keys when it starts, before it answers the MCP client. On the first read of a
   reference, PAL runs:

   ```
   op read <reference> [--account <PAL_OP_ACCOUNT>]
   ```

   - PAL runs `op` directly, with no shell. The reference is one argument, so it cannot add a command.
   - The stdin of `op` is `/dev/null`, because the stdin of PAL carries the MCP messages.
   - `op` gets the environment of PAL, so `op` settings such as `OP_SERVICE_ACCOUNT_TOKEN` apply.
3. If 1Password needs your approval, it shows a Touch ID prompt. PAL waits up to `PAL_OP_TIMEOUT_SECONDS`.
4. PAL removes one trailing newline from the output and keeps the secret in memory.
5. Later reads of the same reference use the kept secret. `op` runs once per reference for the life of
   the PAL process, also when two variables hold the same reference.
6. If you change the secret in 1Password, restart PAL.

The `clink` tool starts other CLIs, for example the Gemini CLI. These CLIs get the secret, not the
reference. If the read failed, the CLI does not get the variable.

### Startup and client timeouts

PAL answers the MCP client only after its startup reads. While the Touch ID prompt waits, the client also
waits.

- Unlock 1Password before you start the client, or approve the prompt quickly.
- If the client stops waiting before PAL is ready, increase the startup timeout of the client. For Claude
  Code, set the `MCP_TIMEOUT` environment variable (in milliseconds) before you start `claude`.

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

If `op` reads from the wrong account, set `PAL_OP_ACCOUNT`.

## What gets logged

- PAL never logs the secret.
- At startup, PAL logs each API key as `[PRESENT]` or `[MISSING]`, not its value.
- The failure warning shows the variable name, the reference, and the error text from `op`.
- PAL logs some settings that are not secrets with their value, for example `CUSTOM_API_URL` and
  `GEMINI_BASE_URL`. If such a setting holds a reference, the log shows the value that `op` returned.

## Limits

- The Docker image does not include `op`. Use plain values with Docker.
- PAL does not read references in the `env` block of a clink client configuration
  (`conf/cli_clients/*.json`).
- `PAL_OP_ACCOUNT` and `PAL_OP_TIMEOUT_SECONDS` cannot be references.
