"""Open one child coding session for a role. The child does not load the agent tool."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tau_agent.messages import AssistantMessage, TextContent
from tau_agent.session import InMemorySessionStorage
from tau_agent.tools import AgentToolResult
from tau_agent.types import JSONValue
from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths
from tau_coding.session import CodingSession, CodingSessionConfig
from tau_coding.thinking import ThinkingLevel, normalize_thinking_level

from sillward.kernel.runner import Kernel
from sillward.memory.recall import apply as apply_recall
from sillward.memory.store import LessonStore
from sillward.roles.definitions import ROLES, Role, child_role
from sillward.roles.sandbox import tools_for
from sillward.teamwork.routing import choose_role

_PROMPT = Path(__file__).with_name("prompt.py")


async def delegate(
    arguments: Mapping[str, JSONValue],
    *,
    kernel: Kernel,
    store: LessonStore,
    parent: CodingSession,
    paths: TauPaths,
    trusted: bool,
    routing_threshold: float,
) -> AgentToolResult:
    """Run one child role and return its last assistant text.

    Shadow routing keeps the role the parent named. Active routing can replace
    it when the judge's probability reaches ``routing_threshold``.
    """
    role_name = arguments.get("role")
    task = arguments.get("task")
    prefix_arg = arguments.get("prefix")
    if not isinstance(role_name, str) or role_name not in ROLES:
        return _error("agent needs a role: implementer, reviewer, or explorer")
    if not isinstance(task, str) or not task.strip():
        return _error("agent needs the task text")
    prefix = _prefix(parent.cwd, prefix_arg)
    if prefix is None:
        return _error("agent needs a path prefix")

    chosen = choose_role(kernel, role_name, threshold=routing_threshold)
    role = ROLES.get(chosen, ROLES[role_name])
    recalled = apply_recall(kernel, store, task, role=role.name)
    text = await _run_child(
        parent=parent,
        paths=paths,
        trusted=trusted,
        role=role,
        task=recalled,
        prefix=prefix,
    )
    return AgentToolResult(content=[TextContent(text=text or "The child stopped.")])


async def _run_child(
    *,
    parent: CodingSession,
    paths: TauPaths,
    trusted: bool,
    role: Role,
    task: str,
    prefix: Path,
) -> str:
    token = child_role.set(role.name)
    try:
        session = await CodingSession.load(
            CodingSessionConfig(
                provider=parent.provider,
                model=parent.model,
                thinking_level=_thinking(parent.thinking_level),
                storage=InMemorySessionStorage(),
                cwd=parent.cwd,
                tools=tools_for(role, cwd=parent.cwd, prefix=prefix),
                allowed_tool_names=frozenset(role.tools),
                resource_paths=TauResourcePaths(
                    root=paths.home,
                    cwd=parent.cwd,
                    agents_root=paths.agents_home,
                    paths=paths,
                    project_resources_enabled=False,
                ),
                provider_name=parent.provider_name,
                skills_enabled=False,
                extensions_enabled=False,
                extension_paths=(_PROMPT,),
                project_extensions_enabled=False,
                trust_override="approve" if trusted else "decline",
                auto_compact_enabled=False,
            )
        )
    finally:
        child_role.reset(token)

    failed = [
        item.message
        for item in session.resource_diagnostics
        if item.severity == "error" and item.name == _PROMPT.stem
    ]
    if failed:
        raise RuntimeError(failed[0])
    async for _event in session.prompt(task):
        pass
    for message in reversed(session.messages):
        if isinstance(message, AssistantMessage) and message.text.strip():
            return message.text
    return ""


def _prefix(cwd: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _thinking(level: str) -> ThinkingLevel:
    return normalize_thinking_level(level)


def _error(text: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=text)])
