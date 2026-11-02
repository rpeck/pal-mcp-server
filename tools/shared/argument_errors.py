"""Turn invalid tool arguments into one error that tells the calling agent exactly what to fix.

The error lists every problem at once (not only the first), names each parameter, and quotes the start of its
description, so the agent can correct all of them in one retry.
"""

import re
from typing import Any

from jsonschema.validators import validator_for
from pydantic import BaseModel, ValidationError

from tools.models import ToolOutput

from .exceptions import ToolExecutionError

_DESCRIPTION_LIMIT = 160


def _short(description: str) -> str:
    """The first sentence of a parameter description, at most _DESCRIPTION_LIMIT characters."""
    text = " ".join(description.split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= _DESCRIPTION_LIMIT else first[: _DESCRIPTION_LIMIT - 3].rstrip() + "..."


def _line(field: str, problem: str, properties: dict[str, Any]) -> str:
    description = properties.get(field, {}).get("description")
    hint = f' Parameter description: "{_short(description)}"' if description else ""
    return f"- `{field}`: {problem}{hint}"


def schema_problems(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Every way in which ``arguments`` breaks the tool's input schema, one line per problem."""
    properties = schema.get("properties", {})
    validator = validator_for(schema)(schema)
    problems = []
    reported_missing = set()  # jsonschema yields one "required" error per missing field, each with the full list
    for error in sorted(validator.iter_errors(arguments), key=lambda e: [str(part) for part in e.path]):
        if error.validator == "required":
            for field in error.validator_value:
                if isinstance(error.instance, dict) and field not in error.instance and field not in reported_missing:
                    reported_missing.add(field)
                    problems.append(_line(field, "missing. This parameter is required.", properties))
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            for field in sorted(set(error.instance) - set(properties)):
                problems.append(f"- `{field}`: not a parameter of this tool. Remove it.")
        else:
            field = ".".join(str(part) for part in error.path)
            if field:
                problems.append(_line(field, f"{error.message}.", properties))
            else:
                problems.append(f"- arguments: {error.message}.")
    return problems


def pydantic_problems(error: ValidationError) -> list[str]:
    """One line per problem in a request-model ValidationError."""
    problems = []
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"])
        message = re.sub(r"^(Value|Assertion) error, ", "", item["msg"])
        if item["type"] == "missing":
            message = "missing. This parameter is required"
        problems.append(f"- `{field}`: {message}." if field else f"- {message}.")
    return problems


def invalid_arguments_error(tool_name: str, problems: list[str]) -> ToolExecutionError:
    """An isError result that lists every problem and tells the agent to fix them and call again."""
    content = (
        f"Invalid arguments for tool '{tool_name}'. Nothing ran. "
        f"Fix every parameter below, then call '{tool_name}' again:\n" + "\n".join(problems)
    )
    output = ToolOutput(
        status="error",
        content=content,
        content_type="text",
        metadata={"tool_name": tool_name, "error_type": "invalid_arguments"},
    )
    return ToolExecutionError(output.model_dump_json())


def build_request(tool_name: str, model: type[BaseModel], arguments: dict[str, Any]) -> Any:
    """Build a tool's request model; on invalid arguments, raise the actionable error."""
    try:
        return model(**arguments)
    except ValidationError as error:
        raise invalid_arguments_error(tool_name, pydantic_problems(error)) from error
