"""
A continuation sends the conversation history to the model once (issue #26).

On a continuation through the MCP server, server.reconstruct_thread_context stores the user's turn, builds the
conversation history into the prompt and passes it to the tool. SimpleTool must use that prompt as is: before the
fix, its check for the history header never matched the header that build_conversation_history writes, so the
tool stored the whole enhanced prompt as a second user turn and built the history again.

These tests check that the prompt sent to the provider holds the history once, that the thread gains one user
turn with the user's own prompt, that the new files of the turn are still embedded, and that a direct call
(no server, so no embedded history) still builds the history itself.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatTool
from utils.conversation_memory import (
    CONVERSATION_HISTORY_HEADER,
    ConversationTurn,
    ThreadContext,
    add_turn,
    build_conversation_history,
    create_thread,
    get_thread,
)

FIRST_PROMPT = "FIRST_QUESTION_MARK: what does first.py do?"
FIRST_REPLY = "FIRST_REPLY_MARK: it sets a constant."
SECOND_PROMPT = "SECOND_QUESTION_MARK: and what about the rest?"


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


def answering_provider():
    provider = create_mock_provider()
    provider.generate_content.return_value.content = "answer"
    return provider


def sent_prompt(provider) -> str:
    provider.generate_content.assert_called_once()
    return provider.generate_content.call_args.kwargs["prompt"]


async def call_through_server(arguments):
    provider = answering_provider()
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        result = await handle_call_tool("chat", arguments)
    return json.loads(result[0].text), sent_prompt(provider)


def test_history_starts_with_the_shared_header():
    context = ThreadContext(
        thread_id="test-thread",
        created_at="2026-01-01T00:00:00Z",
        last_updated_at="2026-01-01T00:05:00Z",
        tool_name="chat",
        turns=[ConversationTurn(role="user", content="hello", timestamp="2026-01-01T00:01:00Z")],
        initial_context={},
    )

    history, _ = build_conversation_history(context)

    assert history.startswith(CONVERSATION_HISTORY_HEADER)


class TestServerContinuation:
    @pytest.mark.asyncio
    async def test_prompt_holds_the_history_once(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        thread_id = chat_thread(first_file)

        _, prompt = await call_through_server(
            {
                "prompt": SECOND_PROMPT,
                "continuation_id": thread_id,
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(CONVERSATION_HISTORY_HEADER) == 1
        assert prompt.count(FIRST_PROMPT) == 1
        assert prompt.count(FIRST_REPLY) == 1
        assert prompt.count("FIRST_FILE_MARK") == 1
        assert prompt.count(SECOND_PROMPT) == 1

    @pytest.mark.asyncio
    async def test_thread_gains_one_user_turn_with_the_users_own_prompt(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        thread_id = chat_thread(first_file)

        await call_through_server(
            {
                "prompt": SECOND_PROMPT,
                "continuation_id": thread_id,
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        new_turns = get_thread(thread_id).turns[2:]
        assert [turn.role for turn in new_turns] == ["user", "assistant"]
        assert new_turns[0].content == SECOND_PROMPT

    @pytest.mark.asyncio
    async def test_new_file_of_the_turn_is_embedded_once(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        new_file = write_file(workspace, "new.py", "NEW_FILE_MARK = 2\n")
        thread_id = chat_thread(first_file)

        output, prompt = await call_through_server(
            {
                "prompt": SECOND_PROMPT,
                "continuation_id": thread_id,
                "absolute_file_paths": [new_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(CONVERSATION_HISTORY_HEADER) == 1
        assert prompt.count("NEW_FILE_MARK") == 1
        assert prompt.count("FIRST_FILE_MARK") == 1
        metadata = output["metadata"]
        assert metadata["embedded_files"] == [first_file, new_file]
        assert metadata["omitted_files"] == []

    @pytest.mark.asyncio
    async def test_file_of_an_earlier_turn_is_not_embedded_again(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        thread_id = chat_thread(first_file)

        output, prompt = await call_through_server(
            {
                "prompt": SECOND_PROMPT,
                "continuation_id": thread_id,
                "absolute_file_paths": [first_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count("FIRST_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [first_file]

    @pytest.mark.asyncio
    async def test_prompt_file_keeps_the_history(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        prompt_file = write_file(workspace, "prompt.txt", "PROMPT_FILE_MARK: the long question\n")
        thread_id = chat_thread(first_file)

        _, prompt = await call_through_server(
            {
                "prompt": "See prompt.txt",
                "continuation_id": thread_id,
                "absolute_file_paths": [prompt_file],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        assert prompt.count(CONVERSATION_HISTORY_HEADER) == 1
        assert prompt.count(FIRST_REPLY) == 1
        assert "PROMPT_FILE_MARK: the long question" in prompt


class TestDirectContinuation:
    """A call that does not pass through server.py gets no embedded history, so the tool builds it."""

    @pytest.mark.asyncio
    async def test_tool_builds_the_history_and_stores_the_user_turn(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        thread_id = chat_thread(first_file)
        provider = answering_provider()

        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
            await ChatTool().execute(
                {
                    "prompt": SECOND_PROMPT,
                    "continuation_id": thread_id,
                    "model": "gemini-2.5-flash",
                    "working_directory_absolute_path": str(workspace),
                }
            )

        prompt = sent_prompt(provider)
        assert prompt.count(CONVERSATION_HISTORY_HEADER) == 1
        assert prompt.count(FIRST_REPLY) == 1
        assert prompt.count("FIRST_FILE_MARK") == 1
        new_turns = get_thread(thread_id).turns[2:]
        assert [turn.role for turn in new_turns] == ["user", "assistant"]
        assert new_turns[0].content == SECOND_PROMPT
