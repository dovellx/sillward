"""tool.approval on bash and on writes that leave the project."""

from __future__ import annotations

from tau_coding.extensions.api import ExtensionContext, ToolCallHookEvent, ToolCallHookResult

from sillward.config import Config
from sillward.gates.state import LoopState
from sillward.gates.support import (
    bash_command,
    choice,
    commit_gate,
    inside_project,
    is_project_check,
    resolved_path,
)
from sillward.kernel.runner import Kernel

_CANDIDATES = ("allow", "deny")
_TOOLS = frozenset({"bash", "write", "edit"})


async def consider(
    kernel: Kernel,
    config: Config,
    state: LoopState,
    event: ToolCallHookEvent,
    context: ExtensionContext,
) -> ToolCallHookResult | None:
    """Block, ask, or let the call through. In-project writes are not asked."""
    if event.tool_name not in _TOOLS or config.mode("tool.approval") == "off":
        return None
    if event.tool_name in {"write", "edit"}:
        path = resolved_path(context.cwd, event.arguments.get("path"))
        if path is not None and inside_project(context.cwd, path):
            return None

    command = bash_command(event.tool_name, event.arguments)
    if is_project_check(command):
        threshold = config.thresholds.approval_check
    else:
        threshold = config.thresholds.approval
    detail = command
    if event.tool_name != "bash":
        raw_path = event.arguments.get("path")
        detail = raw_path if isinstance(raw_path, str) else ""
    opened = kernel.open(
        choice(
            "tool.approval",
            _CANDIDATES,
            summary=event.tool_name,
            state={"tool": event.tool_name, "command": detail, "text": state.frame_text},
        )
    )
    if opened.verdict is None or opened.mode != "active":
        decision = commit_gate(kernel, opened, "execute")
        if decision.action == "block":
            return ToolCallHookResult(
                block=True,
                reason="tool.approval: the judge failed, so the call was blocked",
            )
        return None

    probability = float(opened.verdict.probabilities["allow"])
    if opened.verdict.answer == "allow" and probability >= threshold:
        commit_gate(kernel, opened, "execute")
        return None
    if opened.verdict.answer == "allow" and context.has_ui:
        allowed = await context.ui.confirm(
            "tool.approval",
            (
                f"The probability this {event.tool_name} call is needed is "
                f"{probability:.2f}, below {threshold:.2f}. Run it anyway?"
            ),
        )
        if allowed:
            commit_gate(kernel, opened, "execute")
            return None
        commit_gate(kernel, opened, "block")
        return ToolCallHookResult(block=True, reason="tool.approval: the user refused this call")

    commit_gate(kernel, opened, "block")
    if opened.verdict.answer == "allow":
        reason = f"tool.approval: probability {probability:.2f} is below {threshold:.2f}"
    else:
        reason = "tool.approval: the judge denied this call"
    return ToolCallHookResult(block=True, reason=reason)
