"""tool.injection removes paragraphs the judge marks as instructions."""

from __future__ import annotations

from tau_coding.extensions.api import ToolResultHookEvent

from sillward.config import Config
from sillward.gates.support import LONG_OUTPUT, choice, commit_gate, paragraphs
from sillward.kernel.runner import Kernel

_CANDIDATES = ("keep", "drop")


async def revise(kernel: Kernel, config: Config, event: ToolResultHookEvent) -> str | None:
    """Return tool text with marked paragraphs removed, or None to keep it."""
    if config.mode("tool.injection") == "off":
        return None
    text = event.result.text
    if event.tool_name != "bash" and len(text) < LONG_OUTPUT:
        return None
    parts = paragraphs(text)
    if not parts:
        return None
    kept: list[str] = []
    changed = False
    active = False
    for part in parts:
        opened = kernel.open(
            choice(
                "tool.injection",
                _CANDIDATES,
                summary="segment",
                state={"tool": event.tool_name, "text": part},
            )
        )
        if opened.verdict is None or opened.mode != "active":
            commit_gate(kernel, opened, "execute")
            kept.append(part)
            continue
        active = True
        if opened.verdict.answer == "drop":
            commit_gate(kernel, opened, "rewrite")
            changed = True
            continue
        commit_gate(kernel, opened, "execute")
        kept.append(part)
    if not active or not changed:
        return None
    return "\n\n".join(kept)
