"""Concurrent tool calls from one MCP client overlap instead of queuing in the server."""

import asyncio
import copy
import json
import time
from unittest.mock import patch

import pytest

from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider

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


@pytest.mark.asyncio
async def test_two_chat_calls_run_at_the_same_time(tmp_path):
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

    assert (
        elapsed < 1.6 * PROVIDER_DELAY_SECONDS
    ), f"calls queued: {elapsed:.2f}s for two {PROVIDER_DELAY_SECONDS}s calls"

    # Each call keeps its own model: the per-call state of one call does not leak into the other.
    outputs = [json.loads(result[0].text) for result in results]
    assert [output["status"] for output in outputs] == ["continuation_available", "continuation_available"]
    assert "answer from gemini-2.5-flash" in outputs[0]["content"]
    assert "answer from gemini-2.5-pro" in outputs[1]["content"]
    assert [output["metadata"]["model_used"] for output in outputs] == ["gemini-2.5-flash", "gemini-2.5-pro"]
