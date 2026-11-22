"""Append-only ledger of decisions. Default rows keep no request body."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sillward.protocol import Request

# Tool arguments, file bodies, and secrets. A recorded request replaces these
# values. The frame summary is kept: callers pass a summary, not the raw turn.
_REDACTED = "[redacted]"
_SENSITIVE_STATE_KEYS = frozenset(
    {
        "api_key",
        "args",
        "arguments",
        "authorization",
        "body",
        "command",
        "content",
        "diff",
        "file",
        "file_content",
        "file_contents",
        "key",
        "message",
        "original",
        "original_text",
        "output",
        "parameter",
        "parameters",
        "password",
        "prompt",
        "secret",
        "source",
        "text",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """The fields a default ledger line is allowed to store."""

    point: str
    answer: str | None
    probabilities: Mapping[str, float] | None
    elapsed_ms: float | None
    mode: str
    adapter: str | None
    action: str


class Ledger:
    """Write ``.sillward/ledger.jsonl`` under a project directory."""

    def __init__(self, project: Path, *, record_state: bool) -> None:
        self.path = project / ".sillward" / "ledger.jsonl"
        self.record_state = record_state

    def append(self, entry: LedgerEntry, request: Request | None = None) -> None:
        """Append one JSON object. The request is included only when recording state."""
        payload: dict[str, object] = {
            "point": entry.point,
            "answer": entry.answer,
            "probabilities": (None if entry.probabilities is None else dict(entry.probabilities)),
            "elapsed_ms": entry.elapsed_ms,
            "mode": entry.mode,
            "adapter": entry.adapter,
            "action": entry.action,
        }
        if self.record_state:
            if request is None:
                raise ValueError("record_state requires the request")
            payload["request"] = sanitized_request(request)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def sanitized_request(request: Request) -> dict[str, object]:
    """Return the request with tool arguments and file contents removed."""
    return {
        "point": request.point,
        "question": request.question.value,
        "candidates": list(request.candidates),
        "frame_summary": request.frame_summary,
        "state": _sanitize_state(request.state),
    }


def _sanitize_state(state: Mapping[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, value in state.items():
        if key in _SENSITIVE_STATE_KEYS:
            cleaned[key] = _REDACTED
        else:
            cleaned[key] = _sanitize_value(value)
    return cleaned


def _sanitize_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return _sanitize_state(value)
    if isinstance(value, list | tuple):
        return [_sanitize_value(item) for item in value]
    return _REDACTED
