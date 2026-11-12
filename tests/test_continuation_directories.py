"""
A directory passed in absolute_file_paths is embedded on a continuation too (issue #27).

The conversation history embeds only regular files: it reported a directory in the thread as not_accessible and
left its files out, and filter_new_files dropped the directory as already in the thread. add_turn now stores the
files of a directory, as expand_paths finds them, instead of the directory, so the history plans each file.
"""

import json
from unittest.mock import patch

import pytest

from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from utils.conversation_memory import add_turn, create_thread, get_thread


@pytest.fixture
def workspace(tmp_path):
    """A resolved directory, so that paths match the resolved paths that expand_paths returns."""
    directory = (tmp_path / "workspace").resolve()
    directory.mkdir()
    return directory


@pytest.fixture
def package(workspace):
    """A directory with two small files."""
    directory = workspace / "pkg"
    directory.mkdir()
    one = directory / "one.py"
    one.write_text("ONE_MARK = 1\n")
    two = directory / "two.py"
    two.write_text("TWO_MARK = 2\n")
    return {"directory": str(directory), "files": [str(one), str(two)]}


def chat_thread(workspace) -> str:
    """A chat thread with one exchange about first.py, as the first chat call leaves it."""
    first_file = workspace / "first.py"
    first_file.write_text("FIRST_MARK = 0\n")
    thread_id = create_thread("chat", {"prompt": "first"})
    add_turn(thread_id, "user", "first", files=[str(first_file)], tool_name="chat")
    add_turn(
        thread_id,
        "assistant",
        "reply",
        files=[str(first_file)],
        tool_name="chat",
        model_name="gemini-2.5-flash",
        model_provider="google",
    )
    return thread_id


async def call_chat(workspace, prompt, continuation_id=None, files=None):
    """Call chat through the server; return the response metadata and the prompt sent to the provider."""
    provider = create_mock_provider()
    provider.generate_content.return_value.content = "answer"
    arguments = {"prompt": prompt, "model": "gemini-2.5-flash", "working_directory_absolute_path": str(workspace)}
    if continuation_id:
        arguments["continuation_id"] = continuation_id
    if files:
        arguments["absolute_file_paths"] = files
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        result = await handle_call_tool("chat", arguments)
    provider.generate_content.assert_called_once()
    output = json.loads(result[0].text)
    return output, provider.generate_content.call_args.kwargs["prompt"]


def omitted_paths(metadata):
    return [entry["path"] for entry in metadata["omitted_files"]]


class TestDirectoryOnAContinuation:
    @pytest.mark.asyncio
    async def test_files_of_the_directory_are_embedded(self, workspace, package):
        thread_id = chat_thread(workspace)

        output, prompt = await call_chat(workspace, "look at pkg", thread_id, files=[package["directory"]])

        assert prompt.count("ONE_MARK") == 1
        assert prompt.count("TWO_MARK") == 1
        metadata = output["metadata"]
        assert set(package["files"]) <= set(metadata["embedded_files"])
        assert package["directory"] not in omitted_paths(metadata)
        assert metadata["omitted_files"] == []

    @pytest.mark.asyncio
    async def test_thread_stores_the_files_not_the_directory(self, workspace, package):
        thread_id = chat_thread(workspace)

        await call_chat(workspace, "look at pkg", thread_id, files=[package["directory"]])

        user_turn, assistant_turn = get_thread(thread_id).turns[2:]
        assert user_turn.files == package["files"]
        assert assistant_turn.files == package["files"]

    @pytest.mark.asyncio
    async def test_next_continuation_embeds_the_files_from_the_history(self, workspace, package):
        thread_id = chat_thread(workspace)
        await call_chat(workspace, "look at pkg", thread_id, files=[package["directory"]])

        output, prompt = await call_chat(workspace, "and now?", thread_id)

        assert prompt.count("ONE_MARK") == 1
        assert prompt.count("TWO_MARK") == 1
        metadata = output["metadata"]
        assert set(package["files"]) <= set(metadata["embedded_files"])
        assert metadata["omitted_files"] == []

    @pytest.mark.asyncio
    async def test_same_directory_again_is_embedded_once(self, workspace, package):
        thread_id = chat_thread(workspace)
        await call_chat(workspace, "look at pkg", thread_id, files=[package["directory"]])

        output, prompt = await call_chat(workspace, "look at pkg again", thread_id, files=[package["directory"]])

        assert prompt.count("ONE_MARK") == 1
        assert prompt.count("TWO_MARK") == 1
        assert output["metadata"]["omitted_files"] == []


class TestDirectoryOnTheFirstTurn:
    @pytest.mark.asyncio
    async def test_first_turn_embeds_the_files_and_the_continuation_keeps_them(self, workspace, package):
        output, prompt = await call_chat(workspace, "look at pkg", files=[package["directory"]])

        assert prompt.count("ONE_MARK") == 1
        assert prompt.count("TWO_MARK") == 1
        assert output["metadata"]["embedded_files"] == package["files"]
        thread_id = output["continuation_offer"]["continuation_id"]
        assert all(turn.files == package["files"] for turn in get_thread(thread_id).turns)

        output, prompt = await call_chat(workspace, "and now?", thread_id)

        assert prompt.count("ONE_MARK") == 1
        assert prompt.count("TWO_MARK") == 1
        assert output["metadata"]["embedded_files"] == package["files"]
        assert output["metadata"]["omitted_files"] == []


class TestAddTurnFiles:
    def test_directory_is_stored_as_its_files(self, package):
        thread_id = create_thread("chat", {"prompt": "first"})

        add_turn(thread_id, "user", "first", files=[package["directory"]])

        assert get_thread(thread_id).turns[0].files == package["files"]

    def test_other_paths_are_stored_as_given_in_order_without_duplicates(self, workspace, package):
        missing = str(workspace / "deleted.py")
        thread_id = create_thread("chat", {"prompt": "first"})

        add_turn(thread_id, "user", "first", files=[missing, package["files"][1], package["directory"]])

        assert get_thread(thread_id).turns[0].files == [missing, package["files"][1], package["files"][0]]

    def test_directory_without_files_is_stored_as_given(self, workspace):
        empty = workspace / "empty"
        empty.mkdir()
        thread_id = create_thread("chat", {"prompt": "first"})

        add_turn(thread_id, "user", "first", files=[str(empty)])

        # The history then reports the directory as not_accessible, so the client sees that nothing was embedded.
        assert get_thread(thread_id).turns[0].files == [str(empty)]

    def test_workflow_turn_stores_the_files_of_relevant_directories(self, package):
        from tools.codereview import CodeReviewTool

        tool = CodeReviewTool()
        thread_id = create_thread("codereview", {"step": "review"})
        request = tool.get_workflow_request_model()(
            step="review",
            step_number=1,
            total_steps=2,
            next_step_required=True,
            findings="started",
            relevant_files=[package["directory"]],
        )

        tool.store_conversation_turn(thread_id, {"status": "pause_for_code_review"}, request)

        assert get_thread(thread_id).turns[0].files == package["files"]
