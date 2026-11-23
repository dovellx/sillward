"""Tau extension entry. setup registers the parent-loop gates."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from tau_agent.messages import TextContent
from tau_coding.extensions.api import (
    ExtensionAPI,
    ExtensionContext,
    InputEvent,
    ToolCallHookEvent,
    ToolCallHookResult,
    ToolResultHookEvent,
    ToolResultHookResult,
    TurnEndEvent,
)

from sillward.config import Config, load_config
from sillward.gates import admission, approval, constraint, frame, injection, turn
from sillward.gates.state import LoopState
from sillward.gates.support import bash_command, is_project_check
from sillward.judges.factory import select_judge
from sillward.kernel.runner import Kernel


class _ParentLoop:
    """One session's gates. The kernel is built on the first event, after bind."""

    def __init__(self, tau: ExtensionAPI) -> None:
        self._tau = tau
        self._kernel: Kernel | None = None
        self._config: Config | None = None
        self._client: httpx.Client | None = None
        self._state = LoopState()

    def register(self) -> None:
        """Subscribe the four parent-loop hooks."""
        self._tau.on("input", self.on_input)
        self._tau.on("tool_call", self.on_tool_call)
        self._tau.on("tool_result", self.on_tool_result)
        self._tau.on("turn_end", self.on_turn_end)

    def _ready(self, context: ExtensionContext) -> tuple[Kernel, Config]:
        if self._kernel is None or self._config is None:
            self._client = httpx.Client()
            config = load_config(
                home=Path.home(),
                project=context.cwd,
                environ=os.environ,
            )
            judge = select_judge(
                config,
                environ=os.environ,
                tau_home=context.paths.home,
                client=self._client,
            )
            self._config = config
            self._kernel = Kernel(config=config, judge=judge, project=context.cwd)
        return self._kernel, self._config

    async def on_input(self, event: InputEvent, context: ExtensionContext) -> None:
        """Reset the per-message counters, then record a changed task frame."""
        if event.source == "interactive":
            self._state.continue_sent = 0
            self._state.completion_sent = 0
            self._state.verified = False
        kernel, _config = self._ready(context)
        await frame.consider(self._tau, kernel, self._state, event)

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
        """Strip marked paragraphs, then replace unused parts of a long result."""
        kernel, config = self._ready(context)
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
