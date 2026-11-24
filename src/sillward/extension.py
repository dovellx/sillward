"""Tau extension entry. setup registers the parent-loop gates."""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue
from tau_coding.extensions.api import (
    ExtensionAPI,
    ExtensionContext,
    InputEvent,
    InputHookResult,
    ToolCallHookEvent,
    ToolCallHookResult,
    ToolResultHookEvent,
    ToolResultHookResult,
    TurnEndEvent,
)
from tau_coding.session import CodingSession

from sillward.config import Config, load_config
from sillward.gates import admission, approval, constraint, frame, injection, turn
from sillward.gates.state import LoopState
from sillward.gates.support import bash_command, is_project_check
from sillward.judges.factory import select_judge
from sillward.kernel.runner import Kernel
from sillward.memory.capture import consider_user
from sillward.memory.recall import apply as apply_recall
from sillward.memory.store import LessonStore
from sillward.roles.spawn import delegate
from sillward.teamwork import patch


class _ParentLoop:
    """One session's gates. The kernel is built on the first event, after bind."""

    def __init__(self, tau: ExtensionAPI) -> None:
        self._tau = tau
        self._kernel: Kernel | None = None
        self._config: Config | None = None
        self._store: LessonStore | None = None
        self._client: httpx.Client | None = None
        self._state = LoopState()

    def register(self) -> None:
        """Subscribe the parent-loop hooks and the agent tool."""
        self._tau.on("input", self.on_input)
        self._tau.on("tool_call", self.on_tool_call)
        self._tau.on("tool_result", self.on_tool_result)
        self._tau.on("turn_end", self.on_turn_end)
        self._tau.register_tool(self._agent_tool())

    def _agent_tool(self) -> AgentTool:
        loop = self

        async def execute(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ):
            del tool_call_id, signal, on_update
            kernel, config = loop._ready(loop._tau.context)
            if loop._store is None:
                raise RuntimeError("lessons are not ready")
            return await delegate(
                arguments,
                kernel=kernel,
                store=loop._store,
                parent=loop._parent_session(),
                paths=loop._tau.context.paths,
                trusted=loop._tau.context.project_trusted,
                routing_threshold=config.thresholds.routing_override,
            )

        return AgentTool(
            name="agent",
            label="agent",
            description=(
                "Delegate one task to a child role. The role is implementer, reviewer, "
                "or explorer. The task for a reviewer includes the diff, and the task for "
                "an explorer includes the target. prefix is the directory an implementer "
                "may write. The child cannot delegate."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["implementer", "reviewer", "explorer"],
                    },
                    "task": {"type": "string"},
                    "prefix": {"type": "string"},
                },
                "required": ["role", "task", "prefix"],
            },
            execute_fn=execute,
            execution_mode="sequential",
        )

    def _parent_session(self) -> CodingSession:
        """Return the bound coding session. Its provider is what the child reuses."""
        session = self._tau.context._runtime.session_view
        if not isinstance(session, CodingSession):
            raise RuntimeError("delegation needs the bound coding session")
        return session

    def _ready(self, context: ExtensionContext) -> tuple[Kernel, Config]:
        if self._kernel is None or self._config is None:
            self._client = httpx.Client()
            config = load_config(project=context.cwd, environ=os.environ)
            judge = select_judge(
                config,
                environ=os.environ,
                tau_home=context.paths.home,
                client=self._client,
            )
            self._config = config
            self._kernel = Kernel(config=config, judge=judge, project=context.cwd)
            self._store = LessonStore(context.cwd)
        return self._kernel, self._config

    async def on_input(
        self, event: InputEvent, context: ExtensionContext
    ) -> InputHookResult | None:
        """Reset counters, record the frame, then recall and maybe capture a lesson."""
        if event.source == "interactive":
            self._state.continue_sent = 0
            self._state.completion_sent = 0
            self._state.verified = False
        kernel, _config = self._ready(context)
        await frame.consider(self._tau, kernel, self._state, event)
        if event.source != "interactive" or self._store is None:
            return None
        rewritten = apply_recall(kernel, self._store, event.text, role="parent")
        consider_user(kernel, self._store, event.text, writer="parent")
        if rewritten == event.text:
            return None
        return InputHookResult(action="transform", text=rewritten)

    async def on_tool_call(
        self,
        event: ToolCallHookEvent,
        context: ExtensionContext,
    ) -> ToolCallHookResult | None:
        """Approve, then check constraints. A check that runs counts as verification."""
        kernel, config = self._ready(context)
        blocked = await approval.consider(kernel, config, self._state, event, context)
        if blocked is not None:
            return blocked
        blocked = await constraint.consider(kernel, config, self._state, event, context)
        if blocked is not None:
            return blocked
        if is_project_check(bash_command(event.tool_name, event.arguments)):
            self._state.verified = True
        return None

    async def on_tool_result(
        self,
        event: ToolResultHookEvent,
        context: ExtensionContext,
    ) -> ToolResultHookResult | None:
        """Revise tool text, and record an implementer patch without rejecting it."""
        kernel, config = self._ready(context)
        patch.consider(kernel, event)
        injected = await injection.revise(kernel, config, event)
        current = event
        if injected is not None:
            current = ToolResultHookEvent(
                tool_name=event.tool_name,
                arguments=event.arguments,
                result=event.result.model_copy(update={"content": [TextContent(text=injected)]}),
            )
        admitted = await admission.revise(kernel, config, current)
        final = admitted if admitted is not None else injected
        if final is None:
            return None
        return ToolResultHookResult(content=final)

    async def on_turn_end(self, event: TurnEndEvent, context: ExtensionContext) -> None:
        """Send a stopped turn back when continue or completion says so."""
        kernel, config = self._ready(context)
        await turn.on_stop(self._tau, kernel, config, self._state, event)


def setup(tau: ExtensionAPI) -> None:
    """Register the parent-loop gates. The kernel waits until the session is bound."""
    _ParentLoop(tau).register()
