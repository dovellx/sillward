"""swarm.patch reads an implementer result and does not reject it in shadow."""

from __future__ import annotations

from tau_coding.extensions.api import ToolResultHookEvent

from sillward.gates.support import choice, commit_gate
from sillward.kernel.runner import Kernel

_CANDIDATES = ("within", "beyond")


def consider(kernel: Kernel, event: ToolResultHookEvent) -> None:
    """Record whether an implementer result stays inside the task. Do not change it."""
    if event.tool_name != "agent" or event.arguments.get("role") != "implementer":
        return
    prefix = event.arguments.get("prefix")
    opened = kernel.open(
        choice(
            "swarm.patch",
            _CANDIDATES,
            summary="implementer",
            state={
                "text": event.result.text,
                "prefix": prefix if isinstance(prefix, str) else "",
            },
        )
    )
    commit_gate(kernel, opened, "unchanged")
