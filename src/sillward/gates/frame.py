"""task.frame stores a changed task in the session and leaves the user text alone."""

from __future__ import annotations

from tau_coding.extensions.api import ExtensionAPI, InputEvent

from sillward.gates.state import LoopState
from sillward.gates.support import choice, commit_gate
from sillward.kernel.runner import Kernel

_CANDIDATES = ("new_task", "constraint", "correction", "subgoal", "unchanged")
_NAMESPACE = "sillward.frame"


async def consider(
    tau: ExtensionAPI,
    kernel: Kernel,
    state: LoopState,
    event: InputEvent,
) -> None:
    """Ask what this user message does to the frame. Append only a real change."""
    opened = kernel.open(
        choice(
            "task.frame",
            _CANDIDATES,
            summary="task",
            state={"text": event.text},
        )
    )
    if opened.verdict is None or opened.mode != "active":
        commit_gate(kernel, opened, "unchanged")
        return
    answer = opened.verdict.answer
    if answer == "unchanged" or (answer == state.frame_kind and event.text == state.frame_text):
        commit_gate(kernel, opened, "unchanged")
        return
    commit_gate(kernel, opened, "rewrite")
    state.frame_kind = answer
    state.frame_text = event.text
    await tau.append_entry(_NAMESPACE, {"kind": answer, "text": event.text})
