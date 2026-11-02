"""A call with invalid arguments fails before anything runs, with one error that lists every problem."""

import json

import pytest

from server import handle_call_tool
from tools.shared.exceptions import ToolExecutionError


async def error_for(tool, arguments):
    with pytest.raises(ToolExecutionError) as raised:
        await handle_call_tool(tool, arguments)
    payload = json.loads(raised.value.payload)
    assert payload["status"] == "error"
    assert payload["metadata"]["error_type"] == "invalid_arguments"
    return payload["content"]


@pytest.mark.asyncio
async def test_every_missing_required_parameter_is_listed_once_with_its_description():
    content = await error_for("chat", {"model": "flash"})

    assert content.startswith("Invalid arguments for tool 'chat'. Nothing ran.")
    assert "then call 'chat' again" in content
    assert content.count("- `prompt`: missing. This parameter is required.") == 1
    assert content.count("- `working_directory_absolute_path`: missing. This parameter is required.") == 1
    assert 'Parameter description: "Absolute path to an existing directory' in content


@pytest.mark.asyncio
async def test_a_wrong_type_names_the_parameter():
    content = await error_for(
        "thinkdeep",
        {
            "step": "s",
            "step_number": "abc",
            "total_steps": 1,
            "next_step_required": False,
            "findings": "f",
            "model": "flash",
        },
    )

    assert "- `step_number`: 'abc' is not of type 'integer'." in content


@pytest.mark.asyncio
async def test_an_unknown_parameter_is_named_with_the_fix():
    content = await error_for(
        "chat", {"prompt": "p", "model": "flash", "working_directory_absolute_path": "/tmp", "x": 1}
    )

    assert "- `x`: not a parameter of this tool. Remove it." in content


@pytest.mark.asyncio
async def test_a_rule_that_the_tool_checks_reads_as_a_plain_sentence():
    content = await error_for(
        "consensus", {"step": "s", "step_number": 1, "total_steps": 2, "next_step_required": True, "findings": "f"}
    )

    assert "Step 1 requires 'models' field" in content
    assert "pydantic" not in content and "input_value" not in content


@pytest.mark.asyncio
async def test_a_missing_file_and_a_missing_working_directory_are_rejected_before_anything_runs(tmp_path):
    existing = tmp_path / "exists.py"
    existing.write_text("x = 1\n")
    missing_dir = tmp_path / "no-such-dir"

    content = await error_for(
        "chat",
        {
            "prompt": "p",
            "model": "flash",
            "working_directory_absolute_path": str(missing_dir),
            "absolute_file_paths": [str(existing), "/no/such/file.py", "relative/file.py"],
        },
    )

    assert f"- `working_directory_absolute_path`: does not exist: '{missing_dir}'." in content
    assert "- `absolute_file_paths`: does not exist: '/no/such/file.py'. Pass only paths that exist" in content
    assert "- `absolute_file_paths`: not an absolute path: 'relative/file.py'." in content
    assert str(existing) not in content


@pytest.mark.asyncio
async def test_a_working_directory_that_is_a_file_is_rejected(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("")

    content = await error_for("chat", {"prompt": "p", "model": "flash", "working_directory_absolute_path": str(a_file)})

    assert f"- `working_directory_absolute_path`: not a directory: '{a_file}'. Pass an existing directory." in content


@pytest.mark.asyncio
async def test_path_and_schema_problems_come_back_in_one_error(tmp_path):
    content = await error_for("chat", {"model": "flash", "working_directory_absolute_path": str(tmp_path / "gone")})

    assert "- `prompt`: missing." in content
    assert "- `working_directory_absolute_path`: does not exist:" in content
