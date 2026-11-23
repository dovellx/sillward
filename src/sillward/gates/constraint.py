"""tool.constraint on writes and commands."""

from __future__ import annotations

from tau_coding.extensions.api import ExtensionContext, ToolCallHookEvent, ToolCallHookResult

from sillward.config import Config
from sillward.gates.state import LoopState
from sillward.gates.support import bash_command, choice, commit_gate
from sillward.kernel.runner import Kernel

_CANDIDATES = ("ok", "violation")
_TOOLS = frozenset({"bash", "write", "edit"})


async def consider(
    kernel: Kernel,
    config: Config,
    state: LoopState,
    event: ToolCallHookEvent,
    context: ExtensionContext,
) -> ToolCallHookResult | None:
    """Block a call the judge says violates a stated constraint."""
    del context
    if event.tool_name not in _TOOLS or config.mode("tool.constraint") == "off":
        return None
    command = bash_command(event.tool_name, event.arguments)
    if not command:
        raw_path = event.arguments.get("path")
        command = raw_path if isinstance(raw_path, str) else ""
    opened = kernel.open(
        choice(
            "tool.constraint",
            _CANDIDATES,
            summary="constraint",
            state={"tool": event.tool_name, "command": command, "text": state.frame_text},
        )
    )
    if opened.verdict is None or opened.mode != "active":
        decision = commit_gate(kernel, opened, "execute")
        if decision.action == "block":
            return ToolCallHookResult(
                block=True,
                reason="tool.constraint: the judge failed, so the call was blocked",
            )
        return None
    if opened.verdict.answer == "violation":
        commit_gate(kernel, opened, "block")
        return ToolCallHookResult(
            block=True,
            reason="tool.constraint: the call violates a stated constraint",
        )
    commit_gate(kernel, opened, "execute")
    return None
