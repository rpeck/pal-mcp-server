"""
chat sends its web search instruction to the model once (issue #33).

build_standard_prompt put the instruction after the system prompt and before "=== USER REQUEST ===".
prepare_chat_style_prompt then cut the prompt at that marker, because the provider gets the system prompt
separately, and so it dropped the instruction too. The instruction is now part of the user section.

PAL has no setting that turns the instruction off (use_websearch was removed in v5.12.0). A tool whose
get_websearch_instruction returns "" stands for one that sends none.
"""

from unittest.mock import patch

import pytest

from providers.registry import ModelProviderRegistry
from server import handle_call_tool
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatRequest, ChatTool
from utils.conversation_memory import add_turn, create_thread

WEBSEARCH_MARK = "WEB SEARCH CAPABILITY:"
CHAT_GUIDANCE_MARK = "When discussing topics, consider if searches for these would help:"
USER_PROMPT = "QUESTION_MARK: which queue should we use?"


@pytest.fixture
def workspace(tmp_path):
    directory = (tmp_path / "workspace").resolve()
    directory.mkdir()
    return str(directory)


def answering_provider():
    provider = create_mock_provider()
    provider.generate_content.return_value.content = "answer"
    return provider


def sent_call(provider) -> dict:
    provider.generate_content.assert_called_once()
    return provider.generate_content.call_args.kwargs


async def call_chat(arguments, through_server=False):
    provider = answering_provider()
    with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
        if through_server:
            await handle_call_tool("chat", arguments)
        else:
            await ChatTool().execute(arguments)
    return sent_call(provider)


def chat_thread() -> str:
    thread_id = create_thread("chat", {"prompt": "first"})
    add_turn(thread_id, "user", "first", tool_name="chat")
    add_turn(thread_id, "assistant", "first answer", tool_name="chat", model_name="gemini-2.5-flash")
    return thread_id


class TestChatWebSearchInstruction:
    @pytest.mark.asyncio
    async def test_prompt_holds_the_instruction_once(self, workspace):
        call = await call_chat(
            {"prompt": USER_PROMPT, "model": "gemini-2.5-flash", "working_directory_absolute_path": workspace}
        )

        prompt = call["prompt"]
        assert prompt.count(WEBSEARCH_MARK) == 1
        assert prompt.count(CHAT_GUIDANCE_MARK) == 1
        # The instruction is in the user section, after the request
        assert prompt.index("=== END REQUEST ===") < prompt.index(WEBSEARCH_MARK)
        # The provider gets the system prompt separately, without the instruction
        assert WEBSEARCH_MARK not in call["system_prompt"]

    @pytest.mark.asyncio
    async def test_no_instruction_when_the_tool_sends_none(self, workspace):
        with patch.object(ChatTool, "get_websearch_instruction", return_value=""):
            call = await call_chat(
                {"prompt": USER_PROMPT, "model": "gemini-2.5-flash", "working_directory_absolute_path": workspace}
            )

        assert WEBSEARCH_MARK not in call["prompt"]
        assert CHAT_GUIDANCE_MARK not in call["prompt"]
        assert WEBSEARCH_MARK not in call["system_prompt"]
        assert call["prompt"].count(USER_PROMPT) == 1

    @pytest.mark.asyncio
    async def test_continuation_through_the_server_holds_the_instruction_once(self, workspace):
        call = await call_chat(
            {
                "prompt": USER_PROMPT,
                "continuation_id": chat_thread(),
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": workspace,
            },
            through_server=True,
        )

        assert call["prompt"].count(WEBSEARCH_MARK) == 1
        assert WEBSEARCH_MARK not in call["system_prompt"]

    @pytest.mark.asyncio
    async def test_direct_continuation_holds_the_instruction_once(self, workspace):
        call = await call_chat(
            {
                "prompt": USER_PROMPT,
                "continuation_id": chat_thread(),
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": workspace,
            }
        )

        assert call["prompt"].count(WEBSEARCH_MARK) == 1

    def test_prompt_without_a_system_prompt_holds_the_instruction_once(self, workspace):
        tool = ChatTool()
        tool._current_arguments = {}
        request = ChatRequest(prompt=USER_PROMPT, working_directory_absolute_path=workspace)

        prompt = tool.prepare_chat_style_prompt(request, system_prompt="")

        assert prompt.count(WEBSEARCH_MARK) == 1
        assert prompt.count(USER_PROMPT) == 1
