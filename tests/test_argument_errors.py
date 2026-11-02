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
