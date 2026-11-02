"""A canceled clink call stops the CLI and the child processes that do its work."""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from clink.agents.base import BaseCLIAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole

# A launcher that starts the real work in a child process with the same pipes, as Gemini CLI and npm launchers do.
# The child writes "started", waits 2 seconds, then writes "edited" (a file edit that a cancel must prevent).
LAUNCHER = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    '\'import sys, time, pathlib; d = pathlib.Path(sys.argv[1]); (d / "started").write_text("1"); '
    'time.sleep(2); (d / "edited").write_text("1")\', sys.argv[1]]); time.sleep(30)'
)


@pytest.mark.asyncio
async def test_cancel_stops_the_cli_and_its_children(tmp_path):
    role = ResolvedCLIRole(name="default", prompt_path=Path("systemprompts/clink/codex_default.txt").resolve())
    client = ResolvedCLIClient(
        name="fake",
        executable=[sys.executable],
        internal_args=["-c", LAUNCHER, str(tmp_path)],
        timeout_seconds=60,
        parser="codex_jsonl",
        roles={"default": role},
        working_dir=None,
    )
    task = asyncio.create_task(BaseCLIAgent(client).run(role=role, prompt="edit", files=[], images=[]))
    deadline = time.monotonic() + 10
    while not (tmp_path / "started").exists():
        assert time.monotonic() < deadline, "the fake CLI did not start"
        await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(3)

    assert not (tmp_path / "edited").exists(), "the CLI's child process kept working after the cancel"
    assert not [name for name in os.listdir(tmp_path) if name.startswith("clink-")]
