"""turn.continue and turn.completion send a stopped turn back with a follow-up."""

from __future__ import annotations

from tau_agent.messages import AssistantMessage
from tau_coding.extensions.api import ExtensionAPI, TurnEndEvent

from sillward.config import Config
from sillward.gates.state import LoopState
from sillward.gates.support import choice, commit_gate
from sillward.kernel.runner import Kernel

_CONTINUE = ("continue", "stop")
_COMPLETION = ("send_back", "accept")
_CONTINUE_LIMIT = 2
_CONTINUE_TEXT = "Continue the authorized work. Do the next step now."
_COMPLETION_TEXT = (
    "You claimed the work was complete without a verification action. "
    "Run the project's tests or lint, then continue."
)


async def on_stop(
    tau: ExtensionAPI,
    kernel: Kernel,
    config: Config,
    state: LoopState,
    event: TurnEndEvent,
) -> None:
    """When the assistant stops, maybe send one follow-up."""
    message = event.message
    if not isinstance(message, AssistantMessage):
        return
    if message.tool_calls or message.stop_reason in {"error", "aborted"}:
        return
    if await _completion(tau, kernel, config, state):
        return
    await _continue(tau, kernel, config, state)


async def _completion(
    tau: ExtensionAPI,
    kernel: Kernel,
    config: Config,
    state: LoopState,
) -> bool:
    if config.mode("turn.completion") == "off":
        return False
    opened = kernel.open(
        choice(
            "turn.completion",
            _COMPLETION,
            summary="turn",
            state={"text": state.frame_text, "verified": state.verified},
        )
    )
    will_send = (
        opened.verdict is not None
        and opened.mode == "active"
        and opened.verdict.answer == "send_back"
        and not state.verified
        and state.completion_sent < 1
    )
    if will_send:
        commit_gate(kernel, opened, "follow_up")
        state.completion_sent += 1
        tau.send_user_message(_COMPLETION_TEXT, deliver_as="follow_up")
        return True
    commit_gate(kernel, opened, "unchanged")
    return False


async def _continue(
    tau: ExtensionAPI,
    kernel: Kernel,
    config: Config,
    state: LoopState,
) -> None:
    if config.mode("turn.continue") == "off":
        return
    opened = kernel.open(
        choice(
            "turn.continue",
            _CONTINUE,
            summary="turn",
            state={"text": state.frame_text},
        )
    )
    will_send = (
        opened.verdict is not None
        and opened.mode == "active"
        and opened.verdict.answer == "continue"
        and state.continue_sent < _CONTINUE_LIMIT
    )
    if not will_send:
        commit_gate(kernel, opened, "unchanged")
        return
    commit_gate(kernel, opened, "follow_up")
    state.continue_sent += 1
    tau.send_user_message(_CONTINUE_TEXT, deliver_as="follow_up")
