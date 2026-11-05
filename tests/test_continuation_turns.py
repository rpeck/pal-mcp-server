"""A continued call stores one user turn and one assistant turn, and a new workflow run starts with no steps."""

import copy
import json
from unittest.mock import patch

import pytest

from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from utils.conversation_memory import ConversationTurn, get_thread


def recording_provider(prompts: list[str]):
    """A provider that adds each prompt it gets to ``prompts`` and answers "answer <n>"."""
    provider = create_mock_provider()
    template = provider.generate_content.return_value

    def generate_content(**kwargs):
        prompts.append(kwargs["prompt"])
        response = copy.copy(template)
        response.content = f"answer {len(prompts)}"
        return response

    provider.generate_content.side_effect = generate_content
    return provider


def thread_turns(thread_id: str) -> list[ConversationTurn]:
    thread = get_thread(thread_id)
    assert thread is not None, f"thread {thread_id} not found"
    return thread.turns


async def chat(prompt: str, tmp_path, continuation_id: str | None = None) -> str:
    """Call chat through the server; return the continuation_id of the response."""
    arguments = {"prompt": prompt, "model": "gemini-2.5-flash", "working_directory_absolute_path": str(tmp_path)}
    if continuation_id:
        arguments["continuation_id"] = continuation_id
    result = await handle_call_tool("chat", arguments)
    return json.loads(result[0].text)["continuation_offer"]["continuation_id"]


async def chat_three_exchanges(tmp_path, prompts: list[str]) -> str:
    """Ask Q1, then continue the thread with Q2 and Q3; return the thread ID."""
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=recording_provider(prompts)):
        thread_id = await chat("QUESTION-ONE", tmp_path)
        await chat("QUESTION-TWO", tmp_path, thread_id)
        await chat("QUESTION-THREE", tmp_path, thread_id)
    return thread_id


@pytest.mark.asyncio
async def test_each_continued_chat_exchange_stores_one_user_turn_and_one_assistant_turn(tmp_path):
    thread_id = await chat_three_exchanges(tmp_path, [])

    turns = thread_turns(thread_id)
    assert [turn.role for turn in turns] == ["user", "assistant"] * 3
    assert [turn.content for turn in turns if turn.role == "user"] == ["QUESTION-ONE", "QUESTION-TWO", "QUESTION-THREE"]


@pytest.mark.asyncio
async def test_a_continued_chat_prompt_holds_each_earlier_turn_once(tmp_path):
    prompts: list[str] = []
    await chat_three_exchanges(tmp_path, prompts)

    last_prompt = prompts[-1]
    assert last_prompt.count("=== CONVERSATION HISTORY (CONTINUATION) ===") == 1
    assert last_prompt.count("QUESTION-ONE") == 1
    assert last_prompt.count("QUESTION-TWO") == 1
    assert last_prompt.count("QUESTION-THREE") == 1


async def thinkdeep(step_number: int, text: str, continuation_id: str | None = None) -> str:
    """Send one thinkdeep step through the server; return the continuation_id of the response."""
    arguments = {
        "step": text,
        "step_number": step_number,
        "total_steps": 3,
        "next_step_required": True,
        "findings": f"findings of {text}",
        "model": "gemini-2.5-flash",
    }
    if continuation_id:
        arguments["continuation_id"] = continuation_id
    result = await handle_call_tool("thinkdeep", arguments)
    return json.loads(result[0].text)["continuation_id"]


def saved_steps(thread_id: str) -> list[str]:
    """The steps in the workflow state that the last turn of the thread saved."""
    state = thread_turns(thread_id)[-1].model_metadata
    assert state is not None
    return [step["step"] for step in state["work_history"]]


async def run_one_thinkdeep_investigation() -> None:
    run = await thinkdeep(1, "EARLIER-RUN step 1")
    await thinkdeep(2, "EARLIER-RUN step 2", run)


@pytest.mark.asyncio
async def test_a_new_workflow_run_does_not_keep_the_steps_of_an_earlier_run():
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=create_mock_provider()):
        await run_one_thinkdeep_investigation()
        new_run = await thinkdeep(1, "NEW-RUN step 1")

    assert saved_steps(new_run) == ["NEW-RUN step 1"]


@pytest.mark.asyncio
async def test_a_workflow_run_that_continues_a_chat_thread_does_not_keep_the_steps_of_an_earlier_run(tmp_path):
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=recording_provider([])):
        await run_one_thinkdeep_investigation()
        chat_thread = await chat("QUESTION-ONE", tmp_path)
        await thinkdeep(1, "NEW-RUN step 1", chat_thread)

    assert saved_steps(chat_thread) == ["NEW-RUN step 1"]


@pytest.mark.asyncio
async def test_a_workflow_step_that_continues_its_run_keeps_the_earlier_steps():
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=create_mock_provider()):
        run = await thinkdeep(1, "RUN step 1")
        await thinkdeep(2, "RUN step 2", run)

    assert saved_steps(run) == ["RUN step 1", "RUN step 2"]
