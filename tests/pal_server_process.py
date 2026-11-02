"""Start PAL's server.py as a script over stdio, the way an MCP client does, and read its logs."""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mcp import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent

# Runs server.py as __main__, like `python server.py`. The dotenv readers return nothing, so utils.env reads no .env
# file: a local .env cannot change the test or ask 1Password for a secret.
_LAUNCHER = (
    "import runpy, sys, dotenv; dotenv.load_dotenv = lambda *a, **k: False; dotenv.dotenv_values = lambda *a, **k: {}; "
    "root = sys.argv[1]; sys.path.insert(0, root); sys.argv = [root + '/server.py']; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)


def server_params(log_dir: Path, **env: str) -> StdioServerParameters:
    """Parameters for stdio_client; the server gets only PATH, HOME, the log settings and ``env``."""
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "PAL_TEST_LOG_DIR": str(log_dir)}
    base["LOG_LEVEL"] = "INFO"
    base.update(env)
    return StdioServerParameters(command=sys.executable, args=["-c", _LAUNCHER, str(ROOT)], env=base, cwd=str(ROOT))


def log_rows(path: Path, pattern: str) -> list[tuple[float, str, str]]:
    """(time, request ID, rest of line) for each line of a PAL log that matches ``pattern`` after the request ID."""
    rows = []
    for line in path.read_text().splitlines():
        match = re.match(r"(\S+ \S+),(\d+) - (?:.* - )?\[(\S+)\] (" + pattern + ")", line)
        if match:
            seconds = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp() + int(match.group(2)) / 1000
            rows.append((seconds, match.group(3), match.group(4)))
    return rows


def spans(path: Path, start: str, end: str) -> list[tuple[float, float]]:
    """Sorted (start, end) times per request ID, from the first line that matches ``start`` and ``end``."""
    found: dict[str, list] = {}
    for seconds, request_id, text in log_rows(path, f"{start}|{end}"):
        slot = 0 if re.match(start, text) else 1
        found.setdefault(request_id, [None, None])
        if found[request_id][slot] is None:
            found[request_id][slot] = seconds
    return sorted(tuple(span) for span in found.values())


def descendant_commands(pid: int) -> list[tuple[int, str]]:
    """(pid, command) of every process below ``pid``."""
    listing = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    children: dict[int, list[tuple[int, str]]] = {}
    for row in listing.splitlines():
        parts = row.split(None, 2)
        if len(parts) == 3:
            children.setdefault(int(parts[1]), []).append((int(parts[0]), parts[2]))
    found, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child[0])
    return found
