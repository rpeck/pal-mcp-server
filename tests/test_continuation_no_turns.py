"""
A continuation on a thread with no turns stores the user's turn once (issue #32).

server.reconstruct_thread_context stores the user's turn and builds the history from the turns before it. With no
earlier turns, the history is empty, so the prompt holds no history header. Before the fix, SimpleTool decided from
that header whether server.py had handled the continuation. It found none, so it stored the user's turn a second
time and built the history itself, which repeated the user's prompt.

server.py now marks the arguments with `_original_user_prompt`, and the tool checks that mark, not the prompt text.
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
from tools.clink import CLinkTool
from utils.conversation_memory import CONVERSATION_HISTORY_HEADER, create_thread, get_thread

USER_PROMPT = "ONLY_QUESTION_MARK: what does new.py do?"
FOLLOW_UP_MARK = "CONVERSATION CONTINUATION:"


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


def empty_chat_thread() -> str:
    """A chat thread that holds no turns yet."""
    return create_thread("chat", {"prompt": USER_PROMPT})


async def call_through_server(arguments):
    provider = create_mock_provider()
    provider.generate_content.return_value.content = "answer"
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        result = await handle_call_tool("chat", arguments)
    provider.generate_content.assert_called_once()
    return json.loads(result[0].text), provider.generate_content.call_args.kwargs["prompt"]


class TestServerContinuationWithoutTurns:
    @pytest.mark.asyncio
    async def test_thread_gains_one_user_turn(self, workspace):
        thread_id = empty_chat_thread()

        await call_through_server(
            {
                "prompt": USER_PROMPT,
                "continuation_id": thread_id,
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        turns = get_thread(thread_id).turns
        assert [turn.role for turn in turns] == ["user", "assistant"]
        assert turns[0].content == USER_PROMPT

    @pytest.mark.asyncio
    async def test_prompt_holds_the_users_prompt_once_and_no_history(self, workspace):
        thread_id = empty_chat_thread()

        _, prompt = await call_through_server(
            {
                "prompt": USER_PROMPT,
                "continuation_id": thread_id,
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(USER_PROMPT) == 1
        assert CONVERSATION_HISTORY_HEADER not in prompt
        assert "=== NEW USER INPUT ===" not in prompt
        # The server's follow-up instructions stay in the prompt
        assert prompt.count(FOLLOW_UP_MARK) == 1

    @pytest.mark.asyncio
    async def test_new_file_of_the_turn_is_embedded_once(self, workspace):
        new_file = write_file(workspace, "new.py", "NEW_FILE_MARK = 2\n")
        thread_id = empty_chat_thread()

        output, prompt = await call_through_server(
            {
                "prompt": USER_PROMPT,
                "continuation_id": thread_id,
                "absolute_file_paths": [new_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count("NEW_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [new_file]
        assert output["metadata"]["omitted_files"] == []
        assert get_thread(thread_id).turns[0].files == [new_file]

    @pytest.mark.asyncio
    async def test_prompt_file_keeps_the_follow_up_instructions(self, workspace):
        prompt_file = write_file(workspace, "prompt.txt", "PROMPT_FILE_MARK: the long question\n")
        thread_id = empty_chat_thread()

        _, prompt = await call_through_server(
            {
                "prompt": "See prompt.txt",
                "continuation_id": thread_id,
                "absolute_file_paths": [prompt_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert "PROMPT_FILE_MARK: the long question" in prompt
        assert prompt.count(FOLLOW_UP_MARK) == 1
        assert len(get_thread(thread_id).turns) == 2


class TestClinkPromptFile:
    @pytest.mark.asyncio
    async def test_prompt_file_keeps_the_prompt_that_server_py_prepared(self, workspace, monkeypatch):
        """clink shares the prompt.txt handling: the prompt that server.py prepared stays in front of the file."""
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
        prompt_file = write_file(workspace, "prompt.txt", "PROMPT_FILE_MARK: the long question\n")

        # The arguments as server.py passes them for a thread with no turns: no history, no header
        await CLinkTool().execute(
            {
                "prompt": f"See prompt.txt\n\n{FOLLOW_UP_MARK} the server's instructions",
                "_original_user_prompt": "See prompt.txt",
                "absolute_file_paths": [prompt_file],
                "cli_name": "gemini",
                "role": "default",
            }
        )

        assert sent["prompt"].count(FOLLOW_UP_MARK) == 1
        assert sent["prompt"].count("PROMPT_FILE_MARK: the long question") == 1
