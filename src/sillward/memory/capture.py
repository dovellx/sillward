"""Decide whether a parent message becomes a lesson. Child writes are discarded."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sillward.kernel.runner import Consultation, Decision, Kernel
from sillward.memory.store import Lesson, LessonStore
from sillward.protocol import Question, Request, Verdict

_CAPTURE = ("yes", "no")
_WORTH = ("reusable", "once", "known")


def consider_user(kernel: Kernel, store: LessonStore, text: str, *, writer: str) -> None:
    """Append a user correction when the parent point is active and the judge agrees."""
    _consider(
        kernel,
        store,
        text,
        writer=writer,
        point="memory.capture",
        candidates=_CAPTURE,
        accept="yes",
        source="user",
        summary="user",
    )


def consider_agent(kernel: Kernel, store: LessonStore, text: str, *, writer: str) -> None:
    """Append an agent lesson only when active worth says it is reusable."""
    _consider(
        kernel,
        store,
        text,
        writer=writer,
        point="memory.worth",
        candidates=_WORTH,
        accept="reusable",
        source="agent",
        summary="agent",
    )


def _consider(
    kernel: Kernel,
    store: LessonStore,
    text: str,
    *,
    writer: str,
    point: str,
    candidates: tuple[str, ...],
    accept: str,
    source: str,
    summary: str,
) -> None:
    request = Request(
        point=point,
        question=Question.CHOICE,
        candidates=candidates,
        frame_summary=summary,
        state={"text": text},
    )
    if writer != "parent":
        kernel.discard(request)
        return
    opened = kernel.open(request)
    verdict = opened.verdict
    if (
        verdict is not None
        and opened.mode == "active"
        and verdict.answer == accept
        and _append(store, text, source=source, verdict=verdict)
    ):
        _commit(kernel, opened, "capture")
        return
    _commit(kernel, opened, "unchanged")


def _append(store: LessonStore, text: str, *, source: str, verdict: Verdict) -> bool:
    probability = verdict.probabilities.get(verdict.answer)
    if isinstance(probability, bool) or not isinstance(probability, int | float):
        return False
    return store.append(
        Lesson(
            id=uuid4().hex,
            body=text,
            source=source,
            created_at=datetime.now(UTC).isoformat(),
            probability=float(probability),
        )
    )


def _commit(kernel: Kernel, opened: Consultation, action: str) -> Decision:
    if opened.verdict is None:
        return kernel.commit(opened, action=opened.failure_action)
    if opened.mode != "active":
        return kernel.commit(opened, action="unchanged")
    return kernel.commit(opened, action=action)
