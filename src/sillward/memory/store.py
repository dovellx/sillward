"""Read and append ``.sillward/lessons.jsonl``. Duplicate bodies are not stored."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

_SOURCES = frozenset({"user", "agent"})


def normalize_body(body: str) -> str:
    """Collapse whitespace so two spellings of the same lesson compare equal."""
    return " ".join(body.split())


@dataclass(frozen=True, slots=True)
class Lesson:
    """One stored lesson. ``probability`` is the capture verdict's probability."""

    id: str
    body: str
    source: str
    created_at: str
    probability: float


class LessonStore:
    """Append-only lessons file under a project directory."""

    def __init__(self, project: Path) -> None:
        self.path = project / ".sillward" / "lessons.jsonl"
        self._lock = threading.Lock()

    def load(self) -> list[Lesson]:
        """Return every lesson in file order."""
        with self._lock:
            return self._read()

    def append(self, lesson: Lesson) -> bool:
        """Append ``lesson`` unless its normalized body is already stored.

        An empty body is not stored. ``False`` means nothing was written.
        """
        if lesson.source not in _SOURCES:
            raise ValueError("lesson source must be user or agent")
        key = normalize_body(lesson.body)
        if not key:
            return False
        with self._lock:
            if any(normalize_body(item.body) == key for item in self._read()):
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(_payload(lesson), ensure_ascii=False, separators=(",", ":"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return True

    def _read(self) -> list[Lesson]:
        if not self.path.is_file():
            return []
        lessons: list[Lesson] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("lesson row is not an object")
            lessons.append(_lesson(raw))
        return lessons


def _payload(lesson: Lesson) -> dict[str, object]:
    return {
        "id": lesson.id,
        "body": lesson.body,
        "source": lesson.source,
        "created_at": lesson.created_at,
        "probability": lesson.probability,
    }


def _lesson(raw: dict[str, object]) -> Lesson:
    source = raw.get("source")
    if source not in _SOURCES:
        raise ValueError("lesson source must be user or agent")
    probability = raw.get("probability")
    if isinstance(probability, bool) or not isinstance(probability, int | float):
        raise ValueError("lesson probability is missing")
    body = raw.get("body")
    created_at = raw.get("created_at")
    lesson_id = raw.get("id")
    if (
        not isinstance(body, str)
        or not isinstance(created_at, str)
        or not isinstance(lesson_id, str)
    ):
        raise ValueError("lesson row is missing a field")
    if not lesson_id or not created_at:
        raise ValueError("lesson row is missing a field")
    return Lesson(
        id=lesson_id,
        body=body,
        source=source,
        created_at=created_at,
        probability=float(probability),
    )
