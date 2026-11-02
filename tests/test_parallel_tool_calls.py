"""Concurrent tool calls from one MCP client overlap instead of queuing in the server."""

import asyncio
import contextvars
import copy
import json
import time
from unittest.mock import patch

import pytest

import server
from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatTool
from utils.model_calls import request_id_var

PROVIDER_DELAY_SECONDS = 1.0


def slow_provider():
    """A provider whose blocking generate_content call takes PROVIDER_DELAY_SECONDS and echoes the model name."""
    provider = create_mock_provider()
    template = provider.generate_content.return_value

    def generate_content(**kwargs):
        time.sleep(PROVIDER_DELAY_SECONDS)
        response = copy.copy(template)
        response.content = f"answer from {kwargs['model_name']}"
        return response

    provider.generate_content.side_effect = generate_content
    return provider


async def run_two_chat_calls(tmp_path):
    """Fire two chat calls at once against the slow provider; return the wall time and the two outputs."""
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=slow_provider()):
        start = time.monotonic()
        results = await asyncio.gather(
            handle_call_tool(
                "chat",
                {"prompt": "first", "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)},
            ),
            handle_call_tool(
                "chat",
                {"prompt": "second", "model": "gemini-2.5-pro", "working_directory_absolute_path": str(tmp_path)},
            ),
        )
        elapsed = time.monotonic() - start
    return elapsed, [json.loads(result[0].text) for result in results]


@pytest.mark.asyncio
async def test_two_chat_calls_run_at_the_same_time(tmp_path):
    elapsed, outputs = await run_two_chat_calls(tmp_path)

    assert (
        elapsed < 1.6 * PROVIDER_DELAY_SECONDS
    ), f"calls queued: {elapsed:.2f}s for two {PROVIDER_DELAY_SECONDS}s calls"

    # Each call keeps its own model: the per-call state of one call does not leak into the other.
    assert [output["status"] for output in outputs] == ["continuation_available", "continuation_available"]
    assert "answer from gemini-2.5-flash" in outputs[0]["content"]
    assert "answer from gemini-2.5-pro" in outputs[1]["content"]
    assert [output["metadata"]["model_used"] for output in outputs] == ["gemini-2.5-flash", "gemini-2.5-pro"]


@pytest.mark.asyncio
async def test_kill_switch_runs_one_call_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PARALLEL_TOOL_CALLS", False)
    monkeypatch.setattr(server, "_TOOL_LOCKS", {})

    elapsed, outputs = await run_two_chat_calls(tmp_path)

    assert elapsed >= 1.9 * PROVIDER_DELAY_SECONDS, f"calls overlapped: {elapsed:.2f}s"
    assert [output["metadata"]["model_used"] for output in outputs] == ["gemini-2.5-flash", "gemini-2.5-pro"]


def test_only_tools_without_shared_state_or_file_edits_run_in_parallel():
    assert server.TOOLS["chat"].supports_parallel_calls()
    assert not server.TOOLS["clink"].supports_parallel_calls()  # its CLIs edit files
    assert not server.TOOLS["thinkdeep"].supports_parallel_calls()  # keeps workflow state on the instance
    assert not server.TOOLS["consensus"].supports_parallel_calls()


def test_each_chat_call_writes_its_own_generated_code_file(tmp_path):
    def persist(call_id, block):
        context = contextvars.copy_context()
        context.run(request_id_var.set, call_id)
        return context.run(ChatTool()._persist_generated_code_block, block, str(tmp_path))

    first = persist("aaaa1111", "<GENERATED-CODE>first</GENERATED-CODE>")
    second = persist("bbbb2222", "<GENERATED-CODE>second</GENERATED-CODE>")

    assert (first.name, second.name) == ("pal_generated.aaaa1111.code", "pal_generated.bbbb2222.code")
    assert "first" in first.read_text() and "second" in second.read_text()
