"""
The tool response tells the client which files and turns the model did not receive (issue #15).

On a continuation, build_conversation_history embeds the thread's files newest-first within the file budget and
drops the oldest turns when the history budget is passed; read_files skips new files for the token limit. The
model sees notes about this in the prompt. These tests check that the MCP tool response carries the same facts
in `metadata`: `embedded_files`, `omitted_files` (each with a reason) and `dropped_turns`.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse
from providers.registry import ModelProviderRegistry
from server import handle_call_tool, reconstruct_thread_context
from tests.mock_helpers import create_mock_provider
from tools.chat import ChatTool
from tools.clink import CLinkTool
from tools.codereview import CodeReviewTool
from utils.context_report import ContextReport
from utils.conversation_memory import (
    ConversationTurn,
    ThreadContext,
    add_turn,
    build_conversation_history,
    create_thread,
)
from utils.file_utils import read_files

# A model with a 20K context window: 3,600 file tokens and 6,000 history tokens (see ModelContext).
SMALL_CONTEXT_WINDOW = 20_000

REPORT_KEYS = {"embedded_files", "omitted_files", "dropped_turns"}


@pytest.fixture
def workspace(tmp_path):
    """A resolved directory, so that paths match the resolved paths that read_files embeds."""
    directory = (tmp_path / "workspace").resolve()
    directory.mkdir()
    return directory


def write_file(directory: Path, name: str, size: int) -> str:
    path = directory / name
    path.write_text("x = 1\n" * (size // 6))
    return str(path)


def small_model_provider():
    provider = create_mock_provider(context_window=SMALL_CONTEXT_WINDOW)
    provider.generate_content.return_value.content = "answer"
    return provider


def history_model_context(file_tokens=50_000, history_tokens=50_000):
    """A model context for build_conversation_history that counts 1 token per 4 characters."""
    model_context = Mock()
    model_context.calculate_token_allocation.return_value = Mock(file_tokens=file_tokens, history_tokens=history_tokens)
    model_context.estimate_tokens.side_effect = lambda text: len(text) // 4
    model_context.model_name = "test-model"
    return model_context


def thread(turns):
    return ThreadContext(
        thread_id="test-thread",
        created_at="2026-01-01T00:00:00Z",
        last_updated_at="2026-01-01T00:05:00Z",
        tool_name="chat",
        turns=turns,
        initial_context={},
    )


def user_turn(content, files=None):
    return ConversationTurn(role="user", content=content, timestamp="2026-01-01T00:01:00Z", files=files)


def assistant_turn(content):
    return ConversationTurn(role="assistant", content=content, timestamp="2026-01-01T00:02:00Z", model_name="m")


def omitted(metadata_or_report):
    data = metadata_or_report.to_metadata() if isinstance(metadata_or_report, ContextReport) else metadata_or_report
    return {entry["path"]: entry["reason"] for entry in data["omitted_files"]}


class TestContextReport:
    def test_no_omission_gives_present_and_empty_fields(self):
        assert ContextReport().to_metadata() == {"embedded_files": [], "omitted_files": [], "dropped_turns": 0}

    def test_an_embedded_path_is_never_listed_as_omitted(self):
        report = ContextReport()
        report.omit("/a.py", "token_limit")
        report.embed("/a.py")
        report.omit("/a.py", "missing")

        assert report.to_metadata() == {"embedded_files": ["/a.py"], "omitted_files": [], "dropped_turns": 0}

    def test_merge_keeps_the_first_reason_and_the_order(self):
        report = ContextReport()
        report.embed("/a.py")
        other = ContextReport(dropped_turns=2)
        other.omit("/b.py", "missing")
        other.omit("/b.py", "token_limit")
        other.embed("/c.py")
        report.merge(other)

        assert report.to_metadata() == {
            "embedded_files": ["/a.py", "/c.py"],
            "omitted_files": [{"path": "/b.py", "reason": "missing"}],
            "dropped_turns": 2,
        }


class TestConversationHistoryReport:
    def test_old_file_that_does_not_fit_is_omitted_for_token_limit(self, workspace):
        old_big = write_file(workspace, "old_big.py", 200_000)
        new_small = write_file(workspace, "new_small.py", 120)
        context = thread(
            [
                user_turn("first", files=[old_big]),
                assistant_turn("reply"),
                user_turn("second", files=[new_small]),
            ]
        )
        report = ContextReport()

        history, _ = build_conversation_history(context, history_model_context(file_tokens=2_000), report=report)

        assert report.embedded_files == [new_small]
        assert omitted(report) == {old_big: "token_limit"}
        # The prompt keeps its note for the model.
        assert "1 additional file(s) were omitted" in history

    def test_missing_and_non_regular_files_are_omitted_with_their_reason(self, workspace):
        present = write_file(workspace, "present.py", 120)
        missing = str(workspace / "deleted.py")
        directory = str(workspace)
        context = thread([user_turn("look", files=[present, missing, directory]), assistant_turn("reply")])
        report = ContextReport()

        build_conversation_history(context, history_model_context(), report=report)

        assert report.embedded_files == [present]
        assert omitted(report) == {missing: "missing", directory: "not_accessible"}

    def test_oldest_turns_past_the_history_budget_are_counted(self):
        # Five turns of about 1,000 tokens each; the budget holds the two newest.
        turns = [user_turn(f"turn {i} " + "y" * 4_000) for i in range(5)]
        report = ContextReport()

        history, _ = build_conversation_history(
            thread(turns), history_model_context(history_tokens=2_500), report=report
        )

        assert report.dropped_turns == 3
        assert "[Note: Showing 2 most recent turns out of 5 total]" in history

    def test_without_a_report_the_return_value_is_unchanged(self, workspace):
        context = thread([user_turn("hello", files=[write_file(workspace, "a.py", 120)])])

        result = build_conversation_history(context, history_model_context())

        assert isinstance(result, tuple) and len(result) == 2


class TestReadFilesReport:
    def test_new_file_skipped_for_the_token_limit_is_reported(self, workspace):
        small = write_file(workspace, "a_small.py", 120)
        big = write_file(workspace, "b_big.py", 40_000)
        report = ContextReport()

        content = read_files([small, big], max_tokens=3_000, reserve_tokens=1_000, report=report)

        assert report.embedded_files == [small]
        assert omitted(report) == {big: "token_limit"}
        # The prompt keeps its block for the model.
        assert "--- SKIPPED FILES (TOKEN LIMIT) ---" in content

    def test_missing_path_is_reported(self, workspace):
        small = write_file(workspace, "a_small.py", 120)
        missing = str(workspace / "gone.py")
        report = ContextReport()

        read_files([small, missing], report=report)

        assert report.embedded_files == [small]
        assert omitted(report) == {missing: "missing"}

    def test_directory_is_expanded_to_the_files_it_embeds(self, workspace):
        first = write_file(workspace, "one.py", 120)
        second = write_file(workspace, "two.py", 120)
        report = ContextReport()

        read_files([str(workspace)], report=report)

        assert report.to_metadata() == {"embedded_files": [first, second], "omitted_files": [], "dropped_turns": 0}


class TestServerReconstruction:
    @pytest.mark.asyncio
    async def test_reconstruct_thread_context_carries_the_report(self, workspace):
        old_big = write_file(workspace, "old_big.py", 200_000)
        thread_id = create_thread("chat", {"prompt": "first"})
        add_turn(thread_id, "user", "first", files=[old_big])
        add_turn(thread_id, "assistant", "reply", model_name="gemini-2.5-flash", model_provider="google")

        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=small_model_provider()):
            arguments = await reconstruct_thread_context(
                {"continuation_id": thread_id, "prompt": "second", "model": "gemini-2.5-flash"}
            )

        report = arguments["_context_report"]
        assert isinstance(report, ContextReport)
        assert omitted(report) == {old_big: "token_limit"}


class TestChatResponseMetadata:
    async def run_chat(self, arguments):
        provider = small_model_provider()
        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
            result = await ChatTool().execute(arguments)
        return json.loads(result[0].text)

    @pytest.mark.asyncio
    async def test_continuation_reports_the_old_file_and_the_missing_file(self, workspace):
        old_big = write_file(workspace, "old_big.py", 200_000)
        missing = str(workspace / "deleted.py")
        new_small = write_file(workspace, "new_small.py", 120)
        thread_id = create_thread("chat", {"prompt": "first"})
        add_turn(thread_id, "user", "first", files=[old_big, missing])
        add_turn(thread_id, "assistant", "reply", model_name="gemini-2.5-flash", model_provider="google")

        output = await self.run_chat(
            {
                "prompt": "second",
                "continuation_id": thread_id,
                "absolute_file_paths": [new_small],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        metadata = output["metadata"]
        assert metadata["model_used"] == "gemini-2.5-flash"  # existing keys stay
        assert metadata["embedded_files"] == [new_small]
        assert omitted(metadata) == {old_big: "token_limit", missing: "missing"}
        assert metadata["dropped_turns"] == 0

    @pytest.mark.asyncio
    async def test_new_files_that_read_files_skips_are_reported(self, workspace):
        small = write_file(workspace, "a_small.py", 120)
        big = write_file(workspace, "b_big.py", 40_000)

        output = await self.run_chat(
            {
                "prompt": "look",
                "absolute_file_paths": [small, big],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        metadata = output["metadata"]
        assert metadata["embedded_files"] == [small]
        assert omitted(metadata) == {big: "token_limit"}

    @pytest.mark.asyncio
    async def test_no_omission_gives_present_and_empty_fields(self, workspace):
        small = write_file(workspace, "a_small.py", 120)

        output = await self.run_chat(
            {
                "prompt": "look",
                "absolute_file_paths": [small],
                "model": "gemini-2.5-flash",
                "working_directory_absolute_path": str(workspace),
            }
        )

        metadata = output["metadata"]
        assert metadata["embedded_files"] == [small]
        assert metadata["omitted_files"] == []
        assert metadata["dropped_turns"] == 0

    @pytest.mark.asyncio
    async def test_continuation_through_the_server_reports_history_omissions(self, workspace):
        old_big = write_file(workspace, "old_big.py", 200_000)
        thread_id = create_thread("chat", {"prompt": "first"})
        add_turn(thread_id, "user", "first", files=[old_big])
        add_turn(thread_id, "assistant", "reply", model_name="gemini-2.5-flash", model_provider="google")

        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=small_model_provider()):
            result = await handle_call_tool(
                "chat",
                {
                    "prompt": "second",
                    "continuation_id": thread_id,
                    "model": "gemini-2.5-flash",
                    "working_directory_absolute_path": str(workspace),
                },
            )

        metadata = json.loads(result[0].text)["metadata"]
        assert omitted(metadata) == {old_big: "token_limit"}
        assert metadata["embedded_files"] == []


class TestClinkResponseMetadata:
    @pytest.mark.asyncio
    async def test_clink_reports_the_history_that_the_server_built(self, monkeypatch):
        class DummyAgent:
            async def run(self, **kwargs):
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
        server_report = ContextReport(dropped_turns=1)
        server_report.omit("/old/big.py", "token_limit")

        result = await CLinkTool().execute(
            {"prompt": "second", "cli_name": "gemini", "role": "default", "_context_report": server_report}
        )

        metadata = json.loads(result[0].text)["metadata"]
        assert metadata["cli_name"] == "gemini"  # existing keys stay
        assert omitted(metadata) == {"/old/big.py": "token_limit"}
        assert metadata["dropped_turns"] == 1
        assert metadata["embedded_files"] == []


class TestWorkflowResponseMetadata:
    async def run_codereview(self, arguments):
        provider = small_model_provider()
        with patch.object(ModelProviderRegistry, "get_provider_for_model", return_value=provider):
            result = await CodeReviewTool().execute(arguments)
        return json.loads(result[0].text), provider

    @pytest.mark.asyncio
    async def test_final_step_reports_the_files_the_expert_received(self, workspace):
        small = write_file(workspace, "a_small.py", 120)
        big = write_file(workspace, "b_big.py", 40_000)
        missing = str(workspace / "c_gone.py")

        output, provider = await self.run_codereview(
            {
                "step": "Review the code",
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                "findings": "Looked at the files",
                "relevant_files": [small, big, missing],
                "model": "gemini-2.5-flash",
            }
        )

        provider.generate_content.assert_called_once()
        metadata = output["metadata"]
        assert metadata["tool_name"] == "codereview"  # existing keys stay
        assert metadata["embedded_files"] == [small]
        assert omitted(metadata) == {big: "token_limit", missing: "missing"}
        # Workflow tools send no conversation history to the model.
        assert metadata["dropped_turns"] == 0

    @pytest.mark.asyncio
    async def test_intermediate_step_without_a_model_call_has_empty_fields(self, workspace):
        small = write_file(workspace, "a_small.py", 120)

        output, provider = await self.run_codereview(
            {
                "step": "Start the review",
                "step_number": 1,
                "total_steps": 2,
                "next_step_required": True,
                "findings": "Starting",
                "relevant_files": [small],
                "model": "gemini-2.5-flash",
            }
        )

        provider.generate_content.assert_not_called()
        assert REPORT_KEYS <= output["metadata"].keys()
        assert output["metadata"]["embedded_files"] == []
        assert output["metadata"]["omitted_files"] == []
        assert output["metadata"]["dropped_turns"] == 0
