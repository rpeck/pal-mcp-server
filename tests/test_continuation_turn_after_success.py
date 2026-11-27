"""
A continuation stores the user's turn only when the tool answers (issue #49).

On a continuation through server.py, server.reconstruct_thread_context prepares the user's turn (the prompt, the
prompt.txt text and the files) and builds the history from the turns before it. Before the fix, it also stored the
turn there, before the tool ran. When the tool then rejected the call or failed (the context window check, a
provider error, a CLI error), the thread kept a user turn with no answer after it. The next continuation sent that
request to the model again, and `remaining_turns` counted it.

Now the tool stores the prepared turn just before its own answer: the thread gains a user turn and an assistant
turn, in that order, or no turn. A direct continuation (no server.py) follows the same rule.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clink.agents import AgentOutput, CLIAgentError
from clink.parsers.base import ParsedCLIResponse
from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatTool
from tools.shared.base_tool import user_turn_content
from tools.shared.exceptions import ToolExecutionError
from utils.conversation_memory import MAX_CONVERSATION_TURNS, add_turn, create_thread, get_thread

FIRST_PROMPT = "FIRST_QUESTION_MARK: what does first.py do?"
FIRST_REPLY = "FIRST_REPLY_MARK: it sets a constant."
SECOND_PROMPT = "SECOND_QUESTION_MARK: and what about the rest?"
REJECTED_MARK = "REJECTED_PROMPT_MARK"
# About 17,500 tokens of prompt.txt text do not fit a window of 10,000 tokens
SMALL_WINDOW = 10_000


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


def chat_arguments(workspace: Path, thread_id: str, prompt: str = SECOND_PROMPT, **extra) -> dict:
    return {
        "prompt": prompt,
        "continuation_id": thread_id,
        "model": "gemini-2.5-flash",
        "working_directory_absolute_path": str(workspace),
        **extra,
    }


def large_prompt_file(workspace: Path) -> str:
    """A prompt.txt that does not fit SMALL_WINDOW."""
    return write_file(workspace, "prompt.txt", f"{REJECTED_MARK}\n" + "y" * 70_000)


def turn_count_at_model_call(thread_id: str, counts: list):
    """A provider side effect that records how many turns the thread holds when the model is called."""
    provider = create_mock_provider()
    answer = provider.generate_content.return_value
    answer.content = "answer"

    def generate_content(**kwargs):
        counts.append(len(get_thread(thread_id).turns))
        return answer

    provider.generate_content.side_effect = generate_content
    return provider


async def call_chat(arguments: dict, provider=None, through_server: bool = True):
    """Call chat with a mocked provider. Returns the tool output and the prompt that the provider got."""
    if provider is None:
        provider = create_mock_provider()
        provider.generate_content.return_value.content = "answer"
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        if through_server:
            result = await handle_call_tool("chat", arguments)
        else:
            result = await ChatTool().execute(arguments)
    provider.generate_content.assert_called_once()
    return json.loads(result[0].text), provider.generate_content.call_args.kwargs["prompt"]


async def failed_chat(arguments: dict, provider, through_server: bool = True) -> dict:
    """Call chat, expecting an error. Returns the error payload."""
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        with pytest.raises(ToolExecutionError) as raised:
            if through_server:
                await handle_call_tool("chat", arguments)
            else:
                await ChatTool().execute(arguments)
    return json.loads(raised.value.payload)


def raising_provider():
    provider = create_mock_provider()
    provider.generate_content.side_effect = RuntimeError("PROVIDER_DOWN_MARK")
    return provider


class TestRejectedServerContinuation:
    @pytest.mark.asyncio
    async def test_context_window_rejection_stores_no_turn(self, workspace):
        thread_id = chat_thread()
        provider = create_mock_provider(context_window=SMALL_WINDOW)

        payload = await failed_chat(
            chat_arguments(workspace, thread_id, "See prompt.txt", absolute_file_paths=[large_prompt_file(workspace)]),
            provider,
        )

        assert "context window" in payload["content"]
        provider.generate_content.assert_not_called()
        assert len(get_thread(thread_id).turns) == 2

    @pytest.mark.asyncio
    async def test_provider_error_stores_no_turn(self, workspace):
        thread_id = chat_thread()

        payload = await failed_chat(chat_arguments(workspace, thread_id), raising_provider())

        assert "PROVIDER_DOWN_MARK" in payload["content"]
        assert len(get_thread(thread_id).turns) == 2

    @pytest.mark.asyncio
    async def test_next_continuation_history_does_not_hold_the_rejected_request(self, workspace):
        thread_id = chat_thread()
        await failed_chat(
            chat_arguments(
                workspace,
                thread_id,
                f"{REJECTED_MARK}: see prompt.txt",
                absolute_file_paths=[large_prompt_file(workspace)],
            ),
            create_mock_provider(context_window=SMALL_WINDOW),
        )

        output, prompt = await call_chat(chat_arguments(workspace, thread_id))

        assert REJECTED_MARK not in prompt
        assert prompt.count(FIRST_PROMPT) == 1
        assert prompt.count(SECOND_PROMPT) == 1
        turns = get_thread(thread_id).turns
        assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
        assert turns[2].content == SECOND_PROMPT
        # The rejected call does not count against the turns left
        assert output["continuation_offer"]["remaining_turns"] == MAX_CONVERSATION_TURNS - 4 - 1


class TestSuccessfulServerContinuation:
    @pytest.mark.asyncio
    async def test_one_user_turn_then_one_assistant_turn(self, workspace):
        prompt_file = write_file(workspace, "prompt.txt", "PROMPT_FILE_MARK: the long question")
        thread_id = chat_thread()

        await call_chat(chat_arguments(workspace, thread_id, "See prompt.txt", absolute_file_paths=[prompt_file]))

        new_turns = get_thread(thread_id).turns[2:]
        assert [turn.role for turn in new_turns] == ["user", "assistant"]
        assert new_turns[0].content == user_turn_content("See prompt.txt", "PROMPT_FILE_MARK: the long question")
        assert new_turns[0].files == []
        assert new_turns[1].content.startswith("answer")

    @pytest.mark.asyncio
    async def test_user_turn_is_stored_after_the_model_answers(self, workspace):
        thread_id = chat_thread()
        counts = []

        await call_chat(chat_arguments(workspace, thread_id), turn_count_at_model_call(thread_id, counts))

        assert counts == [2]
        assert len(get_thread(thread_id).turns) == 4

    @pytest.mark.asyncio
    async def test_new_file_is_embedded_once_and_stored_on_the_user_turn(self, workspace):
        first_file = write_file(workspace, "first.py", "FIRST_FILE_MARK = 1\n")
        new_file = write_file(workspace, "new.py", "NEW_FILE_MARK = 2\n")
        thread_id = create_thread("chat", {"prompt": FIRST_PROMPT})
        add_turn(thread_id, "user", FIRST_PROMPT, files=[first_file], tool_name="chat")
        add_turn(
            thread_id, "assistant", FIRST_REPLY, files=[first_file], tool_name="chat", model_name="gemini-2.5-flash"
        )

        output, prompt = await call_chat(chat_arguments(workspace, thread_id, absolute_file_paths=[new_file]))

        assert prompt.count("NEW_FILE_MARK") == 1
        assert prompt.count("FIRST_FILE_MARK") == 1
        assert output["metadata"]["embedded_files"] == [first_file, new_file]
        new_turns = get_thread(thread_id).turns[2:]
        assert [turn.role for turn in new_turns] == ["user", "assistant"]
        assert new_turns[0].files == [new_file]

    @pytest.mark.asyncio
    async def test_empty_thread_gains_one_user_turn(self, workspace):
        thread_id = create_thread("chat", {"prompt": SECOND_PROMPT})

        output, _ = await call_chat(chat_arguments(workspace, thread_id))

        turns = get_thread(thread_id).turns
        assert [turn.role for turn in turns] == ["user", "assistant"]
        assert turns[0].content == SECOND_PROMPT
        assert output["continuation_offer"]["remaining_turns"] == MAX_CONVERSATION_TURNS - 2 - 1


class TestDirectContinuation:
    """A continuation that does not pass through server.py follows the same rule."""

    @pytest.mark.asyncio
    async def test_provider_error_stores_no_turn(self, workspace):
        thread_id = chat_thread()

        await failed_chat(chat_arguments(workspace, thread_id), raising_provider(), through_server=False)

        assert len(get_thread(thread_id).turns) == 2

    @pytest.mark.asyncio
    async def test_success_stores_one_user_turn_after_the_model_answers(self, workspace):
        thread_id = chat_thread()
        counts = []

        await call_chat(
            chat_arguments(workspace, thread_id), turn_count_at_model_call(thread_id, counts), through_server=False
        )

        assert counts == [2]
        new_turns = get_thread(thread_id).turns[2:]
        assert [turn.role for turn in new_turns] == ["user", "assistant"]
        assert new_turns[0].content == SECOND_PROMPT


class TestCrossToolContinuation:
    @pytest.mark.asyncio
    async def test_workflow_step_then_chat_stores_the_chat_user_turn_once(self, workspace):
        """
        A chat thread continued by codereview, then by chat again. The workflow tools take a step, not a prompt, so
        server.py prepares no user turn for them, as before: the codereview step stores its assistant turn only.
        """
        code_file = write_file(workspace, "code.py", "CODE_MARK = 1\n")
        thread_id = chat_thread()
        provider = create_mock_provider()
        provider.generate_content.return_value.content = '{"status": "analysis_complete"}'

        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
            await handle_call_tool(
                "codereview",
                {
                    "step": "Review code.py",
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": "One constant",
                    "relevant_files": [code_file],
                    "continuation_id": thread_id,
                    "model": "gemini-2.5-flash",
                },
            )
        # The final step called the expert model
        provider.generate_content.assert_called_once()

        await call_chat(chat_arguments(workspace, thread_id))

        turns = get_thread(thread_id).turns
        assert [(turn.role, turn.tool_name) for turn in turns[2:]] == [
            ("assistant", "codereview"),
            ("user", None),
            ("assistant", "chat"),
        ]
        assert [turn.content for turn in turns].count(SECOND_PROMPT) == 1

    @pytest.mark.asyncio
    async def test_chat_continues_a_workflow_thread(self, workspace):
        thread_id = create_thread("codereview", {"step": "Review code.py"})
        add_turn(thread_id, "assistant", "REVIEW_MARK: one constant", tool_name="codereview")

        await call_chat(chat_arguments(workspace, thread_id))

        new_turns = get_thread(thread_id).turns[1:]
        assert [turn.role for turn in new_turns] == ["user", "assistant"]
        assert new_turns[0].content == SECOND_PROMPT


class TestToolWithoutAnAnswerTurn:
    @pytest.mark.asyncio
    async def test_challenge_with_a_continuation_stores_no_turn(self):
        """challenge calls no model and stores no answer, so it stores no user turn: the thread gets no turn alone."""
        thread_id = chat_thread()

        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=create_mock_provider()):
            result = await handle_call_tool("challenge", {"prompt": SECOND_PROMPT, "continuation_id": thread_id})

        assert json.loads(result[0].text)["status"] == "challenge_accepted"
        assert len(get_thread(thread_id).turns) == 2


class DummyAgent:
    """A clink CLI agent that answers, or fails with `error`."""

    def __init__(self, error: Exception = None):
        self.error = error

    async def run(self, **kwargs):
        if self.error:
            raise self.error
        return AgentOutput(
            parsed=ParsedCLIResponse(content="CLI_ANSWER_MARK", metadata={}),
            sanitized_command=["gemini"],
            returncode=0,
            stdout="CLI_ANSWER_MARK",
            stderr="",
            duration_seconds=0.1,
            parser_name="gemini_json",
            output_file_content=None,
        )


async def call_clink(arguments: dict):
    provider = create_mock_provider()
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        return await handle_call_tool("clink", arguments)


def clink_arguments(thread_id: str) -> dict:
    return {"prompt": SECOND_PROMPT, "continuation_id": thread_id, "cli_name": "gemini", "role": "default"}


class TestClinkContinuation:
    @pytest.mark.asyncio
    async def test_success_stores_one_user_turn_then_the_answer(self, monkeypatch):
        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())
        thread_id = chat_thread()

        await call_clink(clink_arguments(thread_id))

        new_turns = get_thread(thread_id).turns[2:]
        assert [(turn.role, turn.tool_name) for turn in new_turns] == [("user", None), ("assistant", "clink")]
        assert new_turns[0].content == SECOND_PROMPT
        assert new_turns[1].content == "CLI_ANSWER_MARK"

    @pytest.mark.asyncio
    async def test_cli_error_stores_no_turn(self, monkeypatch):
        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent(CLIAgentError("CLI_DOWN_MARK")))
        thread_id = chat_thread()

        with pytest.raises(ToolExecutionError):
            await call_clink(clink_arguments(thread_id))

        assert len(get_thread(thread_id).turns) == 2

    @pytest.mark.asyncio
    async def test_direct_continuation_stores_one_user_turn_then_the_answer(self, monkeypatch):
        """A direct clink call (no server.py) on a thread stores the user's turn too, just before the answer."""
        from tools.clink import CLinkTool

        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())
        thread_id = chat_thread()

        await CLinkTool().execute(clink_arguments(thread_id))

        new_turns = get_thread(thread_id).turns[2:]
        assert [(turn.role, turn.tool_name) for turn in new_turns] == [("user", None), ("assistant", "clink")]
        assert new_turns[0].content == SECOND_PROMPT

    @pytest.mark.asyncio
    async def test_direct_continuation_with_prompt_file_stores_its_text_not_the_file(self, monkeypatch, workspace):
        from tools.clink import CLinkTool

        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())
        thread_id = chat_thread()
        prompt_file = write_file(workspace, "prompt.txt", "PROMPT_FILE_MARK: the real question")

        await CLinkTool().execute({**clink_arguments(thread_id), "prompt": "", "absolute_file_paths": [prompt_file]})

        user_turn = get_thread(thread_id).turns[2]
        assert user_turn.role == "user"
        assert user_turn.content == "PROMPT_FILE_MARK: the real question"
        assert not user_turn.files

    @pytest.mark.asyncio
    async def test_direct_cli_error_stores_no_turn(self, monkeypatch):
        from tools.clink import CLinkTool

        monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent(CLIAgentError("CLI_DOWN_MARK")))
        thread_id = chat_thread()

        with pytest.raises(ToolExecutionError):
            await CLinkTool().execute(clink_arguments(thread_id))

        assert len(get_thread(thread_id).turns) == 2


class TestBlankPromptContinuation:
    @pytest.mark.asyncio
    async def test_blank_prompt_does_not_crash_reconstruct_thread_context(self):
        """A prompt of only spaces gives an empty user turn; the server must still prepare the call."""
        from server import reconstruct_thread_context

        thread_id = chat_thread()

        enhanced = await reconstruct_thread_context(
            {"prompt": "   ", "continuation_id": thread_id, "model": "gemini-2.5-flash"}
        )

        assert enhanced["_original_user_prompt"] == "   "
        assert not enhanced.get("_pending_user_turn")
        assert len(get_thread(thread_id).turns) == 2
