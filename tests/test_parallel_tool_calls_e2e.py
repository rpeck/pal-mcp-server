"""End-to-end: parallel tool calls, the kill switch, the queue and cancellation in a real server with real models.

The server runs as `python server.py` over stdio, as Claude Code starts it. It gets only an OpenRouter key (cheap
calls to the `flash` alias). The cancel test also runs one ChatGPT call through the Codex CLI and cancels it at once.
Run: python -m pytest tests/test_parallel_tool_calls_e2e.py -m integration
"""

import asyncio
import os
import shutil
import subprocess
import time
from collections import Counter

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import stdio_client

from tests.pal_server_process import descendant_commands, server_params, spans
from utils.env import get_env

pytestmark = pytest.mark.integration

OPENROUTER_API_KEY = get_env("OPENROUTER_API_KEY")
CHEAP_MODEL = "flash"
CODEX_MODEL = "chatgpt/gpt-6-luna"
PROMPT = "Write about 250 words on the history of the {topic}. Plain prose, no code."
TOOL_SPAN = (r"TOOL_CALL: chat ", r"TOOL_COMPLETED: chat")
MODEL_SPAN = (r"Sending request to", r"Received response from")

needs_openrouter = pytest.mark.skipif(not OPENROUTER_API_KEY, reason="needs OPENROUTER_API_KEY")


async def with_server(tmp_path, scenario, **env):
    params = server_params(tmp_path, OPENROUTER_API_KEY=OPENROUTER_API_KEY or "", **env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await scenario(session)


async def two_chats(session, workdir):
    calls = [
        session.call_tool(
            "chat",
            {
                "prompt": PROMPT.format(topic=topic),
                "model": CHEAP_MODEL,
                "working_directory_absolute_path": str(workdir),
            },
        )
        for topic in ("printing press", "telescope")
    ]
    replies = await asyncio.wait_for(asyncio.gather(*calls), timeout=300)
    assert [reply.isError for reply in replies] == [False, False], [reply.content for reply in replies]


@needs_openrouter
@pytest.mark.asyncio
async def test_two_calls_overlap_and_log_once_each(tmp_path):
    async def scenario(session):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert tools["clink"].annotations.readOnlyHint is False
        await two_chats(session, tmp_path)

    await with_server(tmp_path, scenario)

    (first, second) = spans(tmp_path / "mcp_activity.log", *TOOL_SPAN)
    assert second[0] < first[1], f"the calls did not overlap: {first} {second}"
    lines = (tmp_path / "mcp_activity.log").read_text().splitlines()
    assert [line for line, count in Counter(lines).items() if count > 1] == []


@needs_openrouter
@pytest.mark.asyncio
async def test_kill_switch_runs_one_model_call_at_a_time(tmp_path):
    await with_server(tmp_path, lambda session: two_chats(session, tmp_path), PAL_PARALLEL_TOOL_CALLS="false")

    # TOOL_CALL is logged on arrival, before the lock; the model calls show when each call really ran.
    (first, second) = spans(tmp_path / "mcp_server.log", *MODEL_SPAN)
    assert second[0] >= first[1], f"the model calls overlapped: {first} {second}"


@needs_openrouter
@pytest.mark.asyncio
async def test_with_one_worker_the_second_model_call_waits_in_the_queue(tmp_path):
    await with_server(tmp_path, lambda session: two_chats(session, tmp_path), PAL_MAX_PARALLEL_MODEL_CALLS="1")

    assert "waits in the queue" in (tmp_path / "mcp_server.log").read_text()


def codex_problem():
    if not shutil.which("codex"):
        return "the Codex CLI is not installed"
    status = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
    return None if status.returncode == 0 else "the Codex CLI is not signed in"


def codex_processes():
    return [pid for pid, command in descendant_commands(os.getpid()) if "codex" in command]


@needs_openrouter
@pytest.mark.asyncio
async def test_cancel_kills_codex_and_starts_no_retry(tmp_path):
    problem = codex_problem()
    if problem:
        pytest.skip(problem)

    async def scenario(session):
        request_id = session._request_id  # the ID that the next request uses
        call = asyncio.create_task(
            session.call_tool(
                "chat",
                {
                    "prompt": "Write a 3,000-word essay on the history of mathematics.",
                    "model": CODEX_MODEL,
                    "working_directory_absolute_path": str(tmp_path),
                },
            )
        )
        deadline = time.monotonic() + 60
        while not codex_processes() and not call.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        started = codex_processes()
        assert started, "no Codex process started"

        await session.send_notification(
            types.ClientNotification(
                types.CancelledNotification(
                    params=types.CancelledNotificationParams(requestId=request_id, reason="test")
                )
            )
        )
        deadline = time.monotonic() + 10
        while codex_processes() and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        assert not codex_processes(), "a Codex process ran on after the cancel"
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)

        await asyncio.sleep(2)  # a retry, if one started, would show in the log by now
        version = await asyncio.wait_for(session.call_tool("version", {}), timeout=60)
        assert not version.isError, "the server stopped answering after the cancel"
        return started

    started = await with_server(tmp_path, scenario, CHATGPT_ENABLED="true")

    # The killed processes are gone everywhere, not only from under the server.
    listing = subprocess.run(["ps", "-A", "-o", "pid="], capture_output=True, text=True).stdout.split()
    assert not set(map(str, started)) & set(listing)
    text = (tmp_path / "mcp_server.log").read_text()
    assert "canceled the call" in text
    assert "retryable error" not in text.split("canceled the call", 1)[1]
