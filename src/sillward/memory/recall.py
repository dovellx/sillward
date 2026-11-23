"""Choose which stored lessons enter one turn. Parent and child turns both call this."""

from __future__ import annotations

from sillward.kernel.runner import Consultation, Decision, Kernel
from sillward.memory.store import Lesson, LessonStore
from sillward.protocol import Question, Request

_SKIP = "skip"


def apply(kernel: Kernel, store: LessonStore, text: str, *, role: str) -> str:
    """Return ``text`` with the lessons active recall selected for this role.

    Shadow records the lesson id that would have been sent and leaves ``text``
    alone. A child turn uses the same selection as the parent turn.
    """
    if not role.strip():
        raise ValueError("recall needs the turn's role")
    selected: list[Lesson] = []
    for lesson in store.load():
        if lesson.id == _SKIP:
            raise ValueError("lesson id collides with skip")
        opened = kernel.open(
            Request(
                point="memory.recall",
                question=Question.CHOICE,
                candidates=(lesson.id, _SKIP),
                frame_summary="lesson",
                state={"body": lesson.body, "lesson_id": lesson.id},
            )
        )
        verdict = opened.verdict
        if verdict is not None and opened.mode == "active" and verdict.answer == lesson.id:
            _commit(kernel, opened, "recall")
            selected.append(lesson)
            continue
        _commit(kernel, opened, "unchanged")
    if not selected:
        return text
    lines = ["Applicable lessons:", *(f"- {lesson.body}" for lesson in selected), "", text]
    return "\n".join(lines)


def _commit(kernel: Kernel, opened: Consultation, action: str) -> Decision:
    if opened.verdict is None:
        return kernel.commit(opened, action=opened.failure_action)
    if opened.mode != "active":
        return kernel.commit(opened, action="unchanged")
    return kernel.commit(opened, action=action)
