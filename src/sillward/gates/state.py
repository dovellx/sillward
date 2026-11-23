"""Session memory the parent-loop gates share. It is not a lesson."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LoopState:
    """Frame text and the follow-up counters for the current user message."""

    frame_kind: str | None = None
    frame_text: str = ""
    continue_sent: int = 0
    completion_sent: int = 0
    verified: bool = False
