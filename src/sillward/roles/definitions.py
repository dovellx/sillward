"""The three roles a parent may delegate to. This module does not know tau."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Role:
    """One delegated role: the tools it may hold and the text that stays with the session."""

    name: str
    tools: tuple[str, ...]
    instructions: str


IMPLEMENTER = Role(
    name="implementer",
    tools=("read", "write", "edit", "bash"),
    instructions=(
        "You are the implementer. Change only files inside the path prefix you were given. "
        "Bash is not limited to that prefix. Do not delegate."
    ),
)
REVIEWER = Role(
    name="reviewer",
    tools=("read",),
    instructions=(
        "You are the reviewer. Read the diff included in the task. "
        "Do not edit files and do not run commands."
    ),
)
EXPLORER = Role(
    name="explorer",
    tools=("read",),
    instructions=(
        "You are the explorer. Find the target named in the task. "
        "Do not edit files and do not run commands."
    ),
)

ROLES: dict[str, Role] = {role.name: role for role in (IMPLEMENTER, REVIEWER, EXPLORER)}

# Set only while a child session is loading, so its prompt extension can name the role
# without loading the extension that registers the agent tool.
child_role: ContextVar[str | None] = ContextVar("sillward_child_role", default=None)
