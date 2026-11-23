"""Limit a child session's tools, and stop implementer writes that leave the prefix."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue
from tau_coding.tools import create_coding_tools

from sillward.roles.definitions import Role


def tools_for(role: Role, *, cwd: Path, prefix: Path) -> list[AgentTool]:
    """Return the role's tools. Write and edit fail before they touch a path outside ``prefix``."""
    catalog = {tool.name: tool for tool in create_coding_tools(cwd=cwd)}
    selected: list[AgentTool] = []
    for name in role.tools:
        tool = catalog[name]
        if name in {"write", "edit"}:
            tool = _guard(tool, cwd=cwd, prefix=prefix)
        selected.append(tool)
    return selected


def _guard(tool: AgentTool, *, cwd: Path, prefix: Path) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        raw = arguments.get("path")
        if isinstance(raw, str) and raw.strip() and not _inside(prefix, _resolved(cwd, raw)):
            return AgentToolResult(
                content=[TextContent(text=f"{tool.name} blocked: path is outside the prefix")],
            )
        return await tool.execute(tool_call_id, arguments, signal, on_update)

    return replace(tool, execute_fn=execute)


def _resolved(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _inside(prefix: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(prefix.resolve())
    except (OSError, ValueError):
        return False
    return True
