"""
A prompt.txt larger than MCP_PROMPT_SIZE_LIMIT reaches the model (issue #44).

MCP_PROMPT_SIZE_LIMIT is for the text that crosses the MCP transport: the prompt field, or `_original_user_prompt`
on a continuation through server.py. A client writes a prompt that is too large for the transport to prompt.txt, and
PAL reads that file from disk, so its text does not cross the transport. Before, SimpleTool checked the prompt.txt
text against the limit on a first turn and on a direct continuation, and answered `resend_prompt`: it told the
client to use prompt.txt, which it had already done.

The real limit for the prompt.txt text is the model's context window. A prompt that does not fit it gives a clear
error, not `resend_prompt`, and the provider is not called.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse
from config import MCP_PROMPT_SIZE_LIMIT
from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatTool
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError
from utils.conversation_memory import add_turn, create_thread, get_thread

PROMPT_MARK = "PROMPT_FILE_MARK: the long question"
END_MARK = "PROMPT_FILE_END_MARK"
FIRST_PROMPT = "FIRST_QUESTION_MARK: what does first.py do?"
FIRST_REPLY = "FIRST_REPLY_MARK: it sets a constant."
LARGE_SIZE = 70_000


def large_prompt_text(size: int = LARGE_SIZE) -> str:
    """A prompt.txt text of `size` characters, with a mark at each end."""
    filler = "y" * (size - len(PROMPT_MARK) - len(END_MARK) - 2)
    return f"{PROMPT_MARK}\n{filler}\n{END_MARK}"


@pytest.fixture
def workspace(tmp_path):
    """A resolved directory, so that paths match the resolved paths that read_files embeds."""
    directory = (tmp_path / "workspace").resolve()
    directory.mkdir()
    return directory


@pytest.fixture
def large_prompt_file(workspace) -> str:
    path = workspace / "prompt.txt"
    path.write_text(large_prompt_text())
    return str(path)


def chat_thread() -> str:
    """A chat thread with one exchange, as the first chat call leaves it."""
    thread_id = create_thread("chat", {"prompt": FIRST_PROMPT})
    add_turn(thread_id, "user", FIRST_PROMPT, tool_name="chat")
    add_turn(
        thread_id,
        "assistant",
        FIRST_REPLY,
        tool_name="chat",
        model_name="gemini-2.5-flash",
        model_provider="google",
    )
    return thread_id


def chat_arguments(workspace: Path, prompt_file: str, **extra) -> dict:
    return {
        "prompt": "See prompt.txt",
        "absolute_file_paths": [prompt_file],
        "model": "gemini-2.5-flash",
        "working_directory_absolute_path": str(workspace),
        **extra,
    }


async def run_chat(arguments: dict, through_server: bool, provider):
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        if through_server:
            return await handle_call_tool("chat", arguments)
        return await ChatTool().execute(arguments)


async def call_chat(arguments: dict, through_server: bool = True):
    """Call chat with a mocked provider. Returns the tool output and the prompt that the provider got."""
    provider = create_mock_provider()
    provider.generate_content.return_value.content = "answer"
    result = await run_chat(arguments, through_server, provider)
    provider.generate_content.assert_called_once()
    return json.loads(result[0].text), provider.generate_content.call_args.kwargs["prompt"]


async def rejected_chat(arguments: dict, through_server: bool = True, context_window: int = 1_048_576):
    """Call chat with a mocked provider, expecting a rejection. Returns the error payload."""
    provider = create_mock_provider(context_window=context_window)
    with pytest.raises(ToolExecutionError) as raised:
        await run_chat(arguments, through_server, provider)
    provider.generate_content.assert_not_called()
    return json.loads(raised.value.payload)


def assert_prompt_text_sent_once(prompt: str) -> None:
    assert prompt.count(PROMPT_MARK) == 1
    assert prompt.count(END_MARK) == 1
    assert len(prompt) > LARGE_SIZE


class TestLargePromptFileReachesTheModel:
    def test_the_prompt_file_is_over_the_transport_limit(self):
        assert LARGE_SIZE > MCP_PROMPT_SIZE_LIMIT

    @pytest.mark.asyncio
    @pytest.mark.parametrize("through_server", [True, False])
    async def test_first_turn(self, workspace, large_prompt_file, through_server):
        output, prompt = await call_chat(chat_arguments(workspace, large_prompt_file), through_server)

        assert output["status"] == "continuation_available"
        assert_prompt_text_sent_once(prompt)
        assert output["metadata"]["embedded_files"] == []

    @pytest.mark.asyncio
    async def test_direct_continuation(self, workspace, large_prompt_file):
        thread_id = chat_thread()

        output, prompt = await call_chat(
            chat_arguments(workspace, large_prompt_file, continuation_id=thread_id), through_server=False
        )

        assert output["status"] == "continuation_available"
        assert_prompt_text_sent_once(prompt)
        assert FIRST_REPLY in prompt
        # The tool stores the user's turn, with the prompt.txt text, and the reply
        user_turn = get_thread(thread_id).turns[2]
        assert user_turn.content.count(PROMPT_MARK) == 1

    @pytest.mark.asyncio
    async def test_server_continuation(self, workspace, large_prompt_file):
        thread_id = chat_thread()

        output, prompt = await call_chat(chat_arguments(workspace, large_prompt_file, continuation_id=thread_id))

        assert output["status"] == "continuation_available"
        assert_prompt_text_sent_once(prompt)
        assert FIRST_REPLY in prompt

    @pytest.mark.asyncio
    async def test_prompt_file_only_with_an_empty_prompt(self, workspace, large_prompt_file):
        """The resend_prompt message tells the client to leave the prompt empty or very brief."""
        output, prompt = await call_chat(chat_arguments(workspace, large_prompt_file, prompt=""))

        assert output["status"] == "continuation_available"
        assert_prompt_text_sent_once(prompt)


class TestPromptFieldOverTheTransportLimit:
    """The prompt field crosses the transport, so the limit still holds for it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("through_server", [True, False])
    async def test_without_prompt_file(self, workspace, through_server):
        arguments = {
            "prompt": "x" * (MCP_PROMPT_SIZE_LIMIT + 1),
            "model": "gemini-2.5-flash",
            "working_directory_absolute_path": str(workspace),
        }

        payload = await rejected_chat(arguments, through_server)

        assert payload["status"] == "resend_prompt"
        assert payload["metadata"]["prompt_size"] == MCP_PROMPT_SIZE_LIMIT + 1

    @pytest.mark.asyncio
    async def test_with_prompt_file(self, workspace, large_prompt_file):
        """A large prompt field is rejected also when prompt.txt comes with it: the limit counts only the field."""
        arguments = chat_arguments(workspace, large_prompt_file, prompt="x" * (MCP_PROMPT_SIZE_LIMIT + 1))

        payload = await rejected_chat(arguments)

        assert payload["status"] == "resend_prompt"
        assert payload["metadata"]["prompt_size"] == MCP_PROMPT_SIZE_LIMIT + 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("through_server", [True, False])
    async def test_on_a_continuation(self, workspace, through_server):
        thread_id = chat_thread()
        arguments = {
            "prompt": "x" * (MCP_PROMPT_SIZE_LIMIT + 1),
            "continuation_id": thread_id,
            "model": "gemini-2.5-flash",
            "working_directory_absolute_path": str(workspace),
        }

        payload = await rejected_chat(arguments, through_server)

        assert payload["status"] == "resend_prompt"
        assert payload["metadata"]["prompt_size"] == MCP_PROMPT_SIZE_LIMIT + 1


class TestPromptOverTheContextWindow:
    """The model's context window is the real limit for the prompt.txt text."""

    # About 17,500 tokens of prompt.txt text do not fit a window of 10,000 tokens
    SMALL_WINDOW = 10_000

    @pytest.mark.asyncio
    @pytest.mark.parametrize("through_server", [True, False])
    async def test_first_turn_gives_a_clear_error(self, workspace, large_prompt_file, through_server):
        payload = await rejected_chat(
            chat_arguments(workspace, large_prompt_file), through_server, context_window=self.SMALL_WINDOW
        )

        assert payload["status"] == "error"
        assert "context window" in payload["content"]
        assert "gemini-2.5-flash" in payload["content"]
        assert payload["metadata"]["context_window"] == self.SMALL_WINDOW
        assert payload["metadata"]["prompt_tokens"] > self.SMALL_WINDOW

    @pytest.mark.asyncio
    @pytest.mark.parametrize("through_server", [True, False])
    async def test_continuation_gives_a_clear_error(self, workspace, large_prompt_file, through_server):
        thread_id = chat_thread()

        payload = await rejected_chat(
            chat_arguments(workspace, large_prompt_file, continuation_id=thread_id),
            through_server,
            context_window=self.SMALL_WINDOW,
        )

        assert payload["status"] == "error"
        assert "context window" in payload["content"]

    @pytest.mark.asyncio
    async def test_direct_continuation_does_not_store_the_rejected_turn(self, workspace, large_prompt_file):
        thread_id = chat_thread()

        await rejected_chat(
            chat_arguments(workspace, large_prompt_file, continuation_id=thread_id),
            through_server=False,
            context_window=self.SMALL_WINDOW,
        )

        assert len(get_thread(thread_id).turns) == 2

    @pytest.mark.asyncio
    async def test_server_does_not_count_prompt_file_as_a_context_file(self, workspace, large_prompt_file):
        """
        server.py checks the request files against the file budget, a part of the window. prompt.txt holds the
        prompt, not a context file: a prompt.txt that fits the window is not rejected as `code_too_large`.
        """
        # A window of 40,000 tokens gives a file budget of about 4,300 tokens, less than the prompt.txt text
        provider = create_mock_provider(context_window=40_000)
        provider.generate_content.return_value.content = "answer"

        result = await run_chat(chat_arguments(workspace, large_prompt_file), True, provider)

        output = json.loads(result[0].text)
        assert output["status"] == "continuation_available"
        assert_prompt_text_sent_once(provider.generate_content.call_args.kwargs["prompt"])

    @pytest.mark.asyncio
    async def test_server_still_checks_the_other_files(self, workspace, large_prompt_file):
        other_file = workspace / "other.py"
        other_file.write_text("OTHER = 1\n" * 5_000)
        arguments = chat_arguments(workspace, large_prompt_file)
        arguments["absolute_file_paths"].append(str(other_file))

        payload = await rejected_chat(arguments, context_window=40_000)

        assert payload["status"] == "code_too_large"


class DummyAgent:
    """A clink CLI agent that records what it gets."""

    def __init__(self, sent: dict):
        self.sent = sent

    async def run(self, **kwargs):
        self.sent.update(kwargs)
        return AgentOutput(
            parsed=ParsedCLIResponse(content="answer", metadata={}),
            sanitized_command=["gemini"],
            returncode=0,
            stdout="answer",
            stderr="",
            duration_seconds=0.1,
            parser_name="gemini_json",
            output_file_content=None,
        )


class TestClink:
    @pytest.mark.asyncio
    async def test_large_prompt_file_reaches_the_cli(self, large_prompt_file, monkeypatch):
        sent = {}
        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent(sent))

        result = await CLinkTool().execute(
            {
                "prompt": "See prompt.txt",
                "absolute_file_paths": [large_prompt_file],
                "cli_name": "gemini",
                "role": "default",
            }
        )

        assert json.loads(result[0].text)["status"] in {"success", "continuation_available"}
        assert_prompt_text_sent_once(sent["prompt"])

    @pytest.mark.asyncio
    async def test_prompt_field_over_the_limit_asks_for_prompt_file(self, monkeypatch):
        sent = {}
        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent(sent))

        with pytest.raises(ToolExecutionError) as raised:
            await CLinkTool().execute(
                {
                    "prompt": "x" * (MCP_PROMPT_SIZE_LIMIT + 1),
                    "cli_name": "gemini",
                    "role": "default",
                }
            )

        payload = json.loads(raised.value.payload)
        assert payload["status"] == "resend_prompt"
        assert payload["metadata"]["prompt_size"] == MCP_PROMPT_SIZE_LIMIT + 1
        assert sent == {}
