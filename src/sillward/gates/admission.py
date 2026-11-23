"""tool.admission replaces unused chunks of a long tool result with a pointer."""

from __future__ import annotations

from tau_coding.extensions.api import ToolResultHookEvent

from sillward.config import Config
from sillward.gates.support import LONG_OUTPUT, choice, commit_gate, paragraphs
from sillward.kernel.runner import Kernel

_CANDIDATES = ("useful", "later")
_POINTER = "[omitted]"


async def revise(kernel: Kernel, config: Config, event: ToolResultHookEvent) -> str | None:
    """Return the long result with later chunks pointed at, or None to keep it."""
    if config.mode("tool.admission") == "off":
        return None
    text = event.result.text
    if len(text) < LONG_OUTPUT:
        return None
    parts = paragraphs(text)
    if not parts:
        return None
    rebuilt: list[str] = []
    changed = False
    active = False
    for part in parts:
        opened = kernel.open(
            choice(
                "tool.admission",
                _CANDIDATES,
                summary="chunk",
                state={"tool": event.tool_name, "text": part},
            )
        )
        if opened.verdict is None or opened.mode != "active":
            commit_gate(kernel, opened, "execute")
            rebuilt.append(part)
            continue
        active = True
        if opened.verdict.answer == "later":
            commit_gate(kernel, opened, "rewrite")
            rebuilt.append(_POINTER)
            changed = True
            continue
        commit_gate(kernel, opened, "execute")
        rebuilt.append(part)
    if not active or not changed:
        return None
    return "\n\n".join(rebuilt)
