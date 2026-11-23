"""Shared request building and the active/shadow/failure commit rule."""

from __future__ import annotations

import re
from pathlib import Path

from sillward.kernel.runner import Consultation, Decision, Kernel
from sillward.protocol import Question, Request

# Tool results shorter than this stay whole. Admission, and injection of
# non-bash results, start at this length.
LONG_OUTPUT = 4000

_CHECK = re.compile(
    r"""
    ^\s*
    (?:uv\s+run\s+)?
    (?:(?:python|py)(?:\d+(?:\.\d+)*)?(?:\.exe)?\s+-m\s+)?
    (pytest|ruff)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def choice(
    point: str,
    candidates: tuple[str, ...],
    *,
    summary: str,
    state: dict[str, object],
) -> Request:
    """Build a choice request. ``summary`` is a label, not the raw turn."""
    return Request(
        point=point,
        question=Question.CHOICE,
        candidates=candidates,
        frame_summary=summary,
        state=state,
    )


def commit_gate(kernel: Kernel, opened: Consultation, action: str) -> Decision:
    """Write one row.

    A missing verdict keeps the failure action, including a shadow block.
    A recorded verdict in shadow or off does not change behavior.
    """
    if opened.verdict is None:
        return kernel.commit(opened, action=opened.failure_action)
    if opened.mode != "active":
        return kernel.commit(opened, action="unchanged")
    return kernel.commit(opened, action=action)


def paragraphs(text: str) -> list[str]:
    """Split tool output into blank-line paragraphs."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]


def is_project_check(command: str) -> bool:
    """Return whether the command is a project test or lint invocation."""
    return _CHECK.search(command) is not None


def bash_command(tool_name: str, arguments: object) -> str:
    """Return the bash command string, or an empty string for other tools."""
    if tool_name != "bash" or not isinstance(arguments, dict):
        return ""
    command = arguments.get("command")
    return command if isinstance(command, str) else ""


def resolved_path(cwd: Path, raw: object) -> Path | None:
    """Resolve a write/edit path the way the tool will, relative to ``cwd``."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def inside_project(cwd: Path, path: Path) -> bool:
    """Return whether ``path`` stays under the session directory."""
    try:
        path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return False
    return True
