"""
Context report: what the model received in one tool call.

The conversation history and read_files leave files out when they do not fit, are missing, or cannot be read,
and the history drops its oldest turns when they do not fit. The prompt tells the model about this, and the log
records it. A ContextReport collects the same facts while the prompt is built, so that the tool response can
tell the MCP client in `metadata` (issue #15):

- `embedded_files`: the paths whose content the model received, in the order they were embedded.
- `omitted_files`: the paths that were left out, each as {"path": ..., "reason": ...}.
- `dropped_turns`: the number of older turns left out of the conversation history.

The three fields are always present on a response that carries them; a call that omits nothing has empty lists
and 0.
"""

from dataclasses import dataclass, field
from typing import Any

# Reasons for an omitted file
TOKEN_LIMIT = "token_limit"  # The file did not fit in the token budget, or it is larger than the read limit
MISSING = "missing"  # The path does not exist (for example, it was moved or deleted after an earlier turn)
NOT_ACCESSIBLE = "not_accessible"  # The path exists but cannot be read: not a regular file, denied, or an error


@dataclass
class ContextReport:
    """The files embedded, the files omitted with their reason, and the turns dropped for one model call."""

    embedded_files: list[str] = field(default_factory=list)
    omitted_files: dict[str, str] = field(default_factory=dict)  # path -> reason, in the order omitted
    dropped_turns: int = 0

    def embed(self, path: str) -> None:
        """Record that the model received the content of path. A path the model received is not omitted."""
        if path not in self.embedded_files:
            self.embedded_files.append(path)
        self.omitted_files.pop(path, None)

    def omit(self, path: str, reason: str) -> None:
        """Record that path was left out. The first reason for a path wins."""
        if path not in self.embedded_files:
            self.omitted_files.setdefault(path, reason)

    def merge(self, other: "ContextReport") -> None:
        """Add the facts of another report for the same prompt, for example the history that server.py built."""
        for path in other.embedded_files:
            self.embed(path)
        for path, reason in other.omitted_files.items():
            self.omit(path, reason)
        # A prompt holds one conversation history, so the turn counts do not add up.
        self.dropped_turns = max(self.dropped_turns, other.dropped_turns)

    def to_metadata(self) -> dict[str, Any]:
        """The fields for the `metadata` of a tool response."""
        return {
            "embedded_files": list(self.embedded_files),
            "omitted_files": [{"path": path, "reason": reason} for path, reason in self.omitted_files.items()],
            "dropped_turns": self.dropped_turns,
        }
