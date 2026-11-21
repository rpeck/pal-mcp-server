"""
prompt.txt reaches the model once, as the prompt (issue #39).

BaseTool.handle_prompt_file reads prompt.txt and returns the files without it. When prompt.txt was the only file,
it returned None, not an empty list, so SimpleTool kept prompt.txt in the request files. read_files then embedded it
as a context file: the prompt text went to the model twice, embedded_files listed prompt.txt, and the thread stored
it as a file of the turn. This happened on a first turn and on a continuation.

These tests check that the prompt text appears once, that embedded_files and the stored turns do not list
prompt.txt, and that the other files of the request are still embedded once.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse
from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatTool
from tools.clink import CLinkTool
from utils.conversation_memory import add_turn, create_thread, get_thread

PROMPT_MARK = "PROMPT_FILE_MARK: the long question"
FIRST_PROMPT = "FIRST_QUESTION_MARK: what does first.py do?"
FIRST_REPLY = "FIRST_REPLY_MARK: it sets a constant."


@pytest.fixture
def workspace(tmp_path):
    """A resolved directory, so that paths match the resolved paths that read_files embeds."""
    directory = (tmp_path / "workspace").resolve()
    directory.mkdir()
    return directory


def write_file(directory: Path, name: str, text: str) -> str:
    path = directory / name
    path.write_text(text)
    return str(path)


def chat_thread(first_file: str) -> str:
    """A chat thread with one exchange about first_file, as the first chat call leaves it."""
    thread_id = create_thread("chat", {"prompt": FIRST_PROMPT})
    add_turn(thread_id, "user", FIRST_PROMPT, files=[first_file], tool_name="chat")
    add_turn(
        thread_id,
        "assistant",
        FIRST_REPLY,
        files=[first_file],
        tool_name="chat",
        model_name="gemini-2.5-flash",
        model_provider="google",
    )
    return thread_id


async def call_chat(arguments, through_server=True):
    provider = create_mock_provider()
    provider.generate_content.return_value.content = "answer"
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        if through_server:
            result = await handle_call_tool("chat", arguments)
        else:
            result = await ChatTool().execute(arguments)
    provider.generate_content.assert_called_once()
    return json.loads(result[0].text), provider.generate_content.call_args.kwargs["prompt"]


def turn_files(thread_id: str) -> list:
    return [turn.files or [] for turn in get_thread(thread_id).turns]


class TestHandlePromptFile:
    def test_only_prompt_file_gives_an_empty_file_list(self, workspace):
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")

        prompt_content, files = ChatTool().handle_prompt_file([prompt_file])

        assert PROMPT_MARK in prompt_content
        assert files == []

    def test_other_files_stay(self, workspace):
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")
        other_file = write_file(workspace, "other.py", "OTHER_FILE_MARK = 1\n")

        _, files = ChatTool().handle_prompt_file([prompt_file, other_file])

        assert files == [other_file]

    def test_no_files_are_returned_as_given(self):
        assert ChatTool().handle_prompt_file(None) == (None, None)
        assert ChatTool().handle_prompt_file([]) == (None, [])


class TestFirstTurn:
    @pytest.mark.asyncio
    async def test_only_prompt_file_is_sent_once(self, workspace):
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")

        output, prompt = await call_chat(
            {
                "prompt": "See prompt.txt",
                "absolute_file_paths": [prompt_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(PROMPT_MARK) == 1
        assert "CONTEXT FILES" not in prompt
        assert output["metadata"]["embedded_files"] == []
        thread_id = output["continuation_offer"]["continuation_id"]
        assert turn_files(thread_id) == [[], []]

    @pytest.mark.asyncio
    async def test_other_file_is_embedded_once(self, workspace):
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")
        other_file = write_file(workspace, "other.py", "OTHER_FILE_MARK = 1\n")

        output, prompt = await call_chat(
            {
                "prompt": "See prompt.txt",
                "absolute_file_paths": [prompt_file, other_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(PROMPT_MARK) == 1
        assert prompt.count("OTHER_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [other_file]
        thread_id = output["continuation_offer"]["continuation_id"]
        assert turn_files(thread_id) == [[other_file], [other_file]]


class TestServerContinuation:
    @pytest.mark.asyncio
    async def test_only_prompt_file_is_sent_once(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")
        thread_id = chat_thread(first_file)

        output, prompt = await call_chat(
            {
                "prompt": "See prompt.txt",
                "continuation_id": thread_id,
                "absolute_file_paths": [prompt_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(PROMPT_MARK) == 1
        assert prompt.count("FIRST_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [first_file]
        # server.py stores the user's turn, the tool stores the reply: neither lists prompt.txt
        assert turn_files(thread_id)[2:] == [[], []]

    @pytest.mark.asyncio
    async def test_other_file_is_embedded_once(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")
        other_file = write_file(workspace, "other.py", "OTHER_FILE_MARK = 1\n")
        thread_id = chat_thread(first_file)

        output, prompt = await call_chat(
            {
                "prompt": "See prompt.txt",
                "continuation_id": thread_id,
                "absolute_file_paths": [prompt_file, other_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(PROMPT_MARK) == 1
        assert prompt.count("OTHER_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [first_file, other_file]
        assert turn_files(thread_id)[2:] == [[other_file], [other_file]]


class TestDirectContinuation:
    """A call that does not pass through server.py stores the user's turn in the tool."""

    @pytest.mark.asyncio
    async def test_stored_turn_does_not_list_the_prompt_file(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")
        other_file = write_file(workspace, "other.py", "OTHER_FILE_MARK = 1\n")
        thread_id = chat_thread(first_file)

        output, prompt = await call_chat(
            {
                "prompt": "See prompt.txt",
                "continuation_id": thread_id,
                "absolute_file_paths": [prompt_file, other_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            },
            through_server=False,
        )

        assert prompt.count(PROMPT_MARK) == 1
        assert prompt.count("OTHER_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [first_file, other_file]
        assert turn_files(thread_id)[2:] == [[other_file], [other_file]]


class TestClinkPromptFile:
    @pytest.mark.asyncio
    async def test_only_prompt_file_is_not_a_file_reference(self, workspace, monkeypatch):
        """clink lists the request files for the CLI to read: prompt.txt is the prompt, not one of them."""
        sent = {}

        class DummyAgent:
            async def run(self, **kwargs):
                sent.update(kwargs)
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

        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())
        prompt_file = write_file(workspace, "prompt.txt", f"{PROMPT_MARK}\n")

        await CLinkTool().execute(
            {
                "prompt": "See prompt.txt",
                "absolute_file_paths": [prompt_file],
                "cli_name": "gemini",
                "role": "default",
            }
        )

        assert sent["prompt"].count(PROMPT_MARK) == 1
        assert "=== FILE REFERENCES ===" not in sent["prompt"]
