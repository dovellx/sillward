"""Phase 3 exit checks: lessons, recall, capture, and child discard.

A production change that drops an agreed correction, appends a duplicate body,
forgets one of two different lessons, leaves a selected lesson out of a parent
or child turn, or lets a child capture write a file fails these tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import pytest
from tau_agent.messages import AssistantMessage, TextContent, UserMessage
from tau_agent.provider_events import AssistantDoneEvent
from tau_agent.session import InMemorySessionStorage
from tau_coding.extensions.api import NullUiBridge
from tau_coding.extensions.runtime import ExtensionRuntime
from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths
from tau_coding.session import CodingSession, CodingSessionConfig

import sillward.extension as extension_module
from sillward.protocol import Request, Verdict

KEEP = "Keep the public API stable."
DROP = "Rewrite the public API every week."
TASK = "fix the bug"


def _verdict(answer: str, candidates: tuple[str, ...], probability: float = 0.91) -> Verdict:
    others = [candidate for candidate in candidates if candidate != answer]
    share = 0.0 if not others else (1.0 - probability) / len(others)
    probabilities = {
        candidate: (probability if candidate == answer else share) for candidate in candidates
    }
    return Verdict(
        answer=answer,
        probabilities=probabilities,
        elapsed_ms=1.0,
        adapter="fake",
    )


def _lesson_body(request: Request) -> str:
    body = request.state.get("body", request.state.get("text", ""))
    return body if isinstance(body, str) else ""


class MemoryJudge:
    """Fake judge. Capture, worth, and recall answers come from the test."""

    name = "fake"

    def __init__(
        self,
        *,
        capture: str = "yes",
        worth: str | list[str] = "reusable",
        include: set[str] | None = None,
        probability: float = 0.93,
    ) -> None:
        self.capture = capture
        self._worth = list(worth) if isinstance(worth, list) else [worth]
        self.include = set() if include is None else set(include)
        self.probability = probability
        self.requests: list[Request] = []

    def judge(self, request: Request) -> Verdict:
        self.requests.append(request)
        if request.point == "task.frame":
            return _verdict("unchanged", request.candidates)
        if request.point == "turn.continue":
            return _verdict("stop", request.candidates)
        if request.point == "turn.completion":
            return _verdict("accept", request.candidates)
        if request.point == "memory.capture":
            return _verdict(self.capture, request.candidates, self.probability)
        if request.point == "memory.worth":
            answer = self._worth.pop(0)
            return _verdict(answer, request.candidates, self.probability)
        if request.point == "memory.recall":
            skipped = {"skip", "no", "exclude"}
            positive = [item for item in request.candidates if item not in skipped]
            negative = [item for item in request.candidates if item in skipped]
            if _lesson_body(request) in self.include:
                if not positive:
                    raise AssertionError(request.candidates)
                return _verdict(positive[0], request.candidates, 0.9)
            if not negative:
                raise AssertionError(request.candidates)
            return _verdict(negative[0], request.candidates, 0.9)
        raise AssertionError(request.point)


class RaisingJudge:
    """A judge that must not be called."""

    name = "fake"

    def judge(self, request: Request) -> Verdict:
        raise AssertionError(f"asked the judge about {request.point}")


class ScriptedProvider:
    """Fake model. Each call replays the next scripted assistant message."""

    def __init__(self, messages: list[AssistantMessage]) -> None:
        self._messages = messages
        self.calls = 0

    async def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[object],
        tools: list[object],
        signal: object = None,
        session_id: str | None = None,
    ):
        del model, system, messages, tools, signal, session_id
        self.calls += 1
        if self.calls > 6:
            raise RuntimeError(f"provider called {self.calls} times")
        index = min(self.calls, len(self._messages)) - 1
        yield AssistantDoneEvent(reason="stop", message=self._messages[index].model_copy(deep=True))


def _assistant(text: str = "done") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")


def _lessons(project: Path) -> list[dict[str, object]]:
    path = project / ".sillward" / "lessons.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _ledger(project: Path) -> str:
    path = project / ".sillward" / "ledger.jsonl"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _rows(project: Path, point: str) -> list[dict[str, object]]:
    text = _ledger(project)
    if not text:
        return []
    return [
        row
        for line in text.splitlines()
        if line and (row := json.loads(line)).get("point") == point
    ]


def _user_texts(session: CodingSession) -> list[str]:
    return [message.text for message in session.messages if isinstance(message, UserMessage)]


def _seed(project: Path) -> None:
    path = project / ".sillward" / "lessons.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "id": "lesson-keep",
            "body": KEEP,
            "source": "user",
            "created_at": "2026-01-01T00:00:00+00:00",
            "probability": 0.95,
        },
        {
            "id": "lesson-drop",
            "body": DROP,
            "source": "user",
            "created_at": "2026-01-01T00:00:00+00:00",
            "probability": 0.95,
        },
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


async def _open_session(
    tmp: Path,
    *,
    provider: ScriptedProvider,
    judge: object,
    monkeypatch: pytest.MonkeyPatch,
    overlay: Mapping[str, str],
) -> CodingSession:
    home = tmp / "home"
    project = tmp / "project"
    tau_home = tmp / "tau-home"
    agents = tmp / "agents"
    for path in (home, project, tau_home, agents):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TAU_HOME", str(tau_home))
    monkeypatch.delenv("SILLWARD_MODE_OVERLAY", raising=False)
    if overlay:
        monkeypatch.setenv("SILLWARD_MODE_OVERLAY", json.dumps(overlay))
    monkeypatch.setattr(
        "sillward.judges.factory.select_judge",
        lambda config, *, environ, tau_home, client: judge,
    )
    paths = TauPaths(home=tau_home, agents_home=agents)
    storage = InMemorySessionStorage()
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model="fake-model",
            storage=storage,
            cwd=project,
            system="phase 3 test session",
            resource_paths=TauResourcePaths(
                root=tau_home,
                cwd=project,
                agents_root=agents,
                paths=paths,
                project_resources_enabled=False,
            ),
            provider_name="fake",
            skills_enabled=False,
            extensions_enabled=False,
            extension_paths=(Path(extension_module.__file__ or "").resolve(),),
            extension_runtime=ExtensionRuntime(
                ui=NullUiBridge(),
                paths=paths,
                built_in_extensions=(),
            ),
            trust_override="approve",
            auto_compact_enabled=False,
        )
    )
    failures = [
        item.message for item in session.resource_diagnostics if "setup failed" in item.message
    ]
    assert failures == []
    return session


async def _prompt(session: CodingSession, text: str) -> None:
    async for _event in session.prompt(text):
        pass


def _kernel(tmp: Path, judge: object, overlay: Mapping[str, str]):
    from sillward.config import load_config
    from sillward.kernel.runner import Kernel

    home = tmp / "home"
    project = tmp / "project"
    home.mkdir(parents=True, exist_ok=True)
    project.mkdir(parents=True, exist_ok=True)
    config = load_config(
        home=home,
        project=project,
        environ={"SILLWARD_MODE_OVERLAY": json.dumps(overlay)} if overlay else {},
    )
    return Kernel(config=config, judge=judge, project=project), project


def test_agreed_user_correction_appends_one_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignoring a yes, or writing nothing, leaves the lessons file empty."""

    async def body() -> None:
        judge = MemoryJudge(capture="yes", probability=0.93)
        provider = ScriptedProvider([_assistant()])
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=judge,
            monkeypatch=monkeypatch,
            overlay={"memory.capture": "active"},
        )
        await asyncio.wait_for(_prompt(session, "Do not use tabs."), timeout=20)
        project = tmp_path / "project"
        lessons = _lessons(project)
        assert len(lessons) == 1
        lesson = lessons[0]
        assert lesson["body"] == "Do not use tabs."
        assert lesson["source"] == "user"
        assert lesson["probability"] == pytest.approx(0.93)
        assert isinstance(lesson["id"], str) and lesson["id"]
        created_at = lesson["created_at"]
        assert isinstance(created_at, str)
        datetime.fromisoformat(created_at)
        assert "Do not use tabs." not in _ledger(project)
        row = _rows(project, "memory.capture")[0]
        assert row["action"] == "capture"
        assert row["mode"] == "active"
        assert row["answer"] == "yes"

    asyncio.run(body())


def test_same_body_after_whitespace_normalization_does_not_append_a_second_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Appending on exact text only, or on every yes, leaves two lessons."""

    async def body() -> None:
        judge = MemoryJudge(capture="yes")
        provider = ScriptedProvider([_assistant(), _assistant()])
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=judge,
            monkeypatch=monkeypatch,
            overlay={"memory.capture": "active"},
        )
        await asyncio.wait_for(_prompt(session, "Do not use tabs."), timeout=20)
        await asyncio.wait_for(_prompt(session, "Do   not\nuse\ttabs."), timeout=20)
        lessons = _lessons(tmp_path / "project")
        assert len(lessons) == 1
        assert lessons[0]["body"] == "Do not use tabs."

    asyncio.run(body())


def test_different_bodies_are_both_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Merging a contradiction, or keeping only the latest text, drops one lesson."""

    async def body() -> None:
        judge = MemoryJudge(capture="yes")
        provider = ScriptedProvider([_assistant(), _assistant()])
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=judge,
            monkeypatch=monkeypatch,
            overlay={"memory.capture": "active"},
        )
        await asyncio.wait_for(
            _prompt(session, "Always write a regression test before the fix."),
            timeout=20,
        )
        await asyncio.wait_for(
            _prompt(session, "Never write a regression test before the fix."),
            timeout=20,
        )
        bodies = [lesson["body"] for lesson in _lessons(tmp_path / "project")]
        assert bodies == [
            "Always write a regression test before the fix.",
            "Never write a regression test before the fix.",
        ]

    asyncio.run(body())


def test_active_recall_puts_selected_lessons_into_parent_and_child_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving either turn unchanged, or injecting the skipped lesson, hides a selection."""

    async def body() -> None:
        judge = MemoryJudge(include={KEEP})
        provider = ScriptedProvider([_assistant()])
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=judge,
            monkeypatch=monkeypatch,
            overlay={"memory.recall": "active", "memory.capture": "off"},
        )
        project = tmp_path / "project"
        _seed(project)
        await asyncio.wait_for(_prompt(session, TASK), timeout=20)
        texts = _user_texts(session)
        assert any(KEEP in text and TASK in text for text in texts)
        assert all(DROP not in text for text in texts)
        kept = [row for row in _rows(project, "memory.recall") if row["answer"] == "lesson-keep"]
        assert kept
        assert kept[0]["action"] == "recall"
        assert KEEP not in _ledger(project)
        assert DROP not in _ledger(project)

        from sillward.memory.recall import apply
        from sillward.memory.store import LessonStore

        child_judge = MemoryJudge(include={KEEP})
        child_kernel, child_project = _kernel(
            tmp_path / "child",
            child_judge,
            {"memory.recall": "active"},
        )
        _seed(child_project)
        child = apply(child_kernel, LessonStore(child_project), TASK, role="child")
        assert KEEP in child
        assert TASK in child
        assert DROP not in child

    asyncio.run(body())


def test_shadow_recall_records_the_lesson_and_leaves_the_message_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injecting during shadow, or skipping the ledger, changes the turn or hides the id."""

    async def body() -> None:
        judge = MemoryJudge(include={KEEP})
        provider = ScriptedProvider([_assistant()])
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=judge,
            monkeypatch=monkeypatch,
            overlay={"memory.recall": "shadow", "memory.capture": "off"},
        )
        project = tmp_path / "project"
        _seed(project)
        await asyncio.wait_for(_prompt(session, TASK), timeout=20)
        assert _user_texts(session) == [TASK]
        answers = {row["answer"] for row in _rows(project, "memory.recall")}
        assert "lesson-keep" in answers
        assert KEEP not in _ledger(project)

    asyncio.run(body())


def test_shadow_capture_does_not_write_a_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing the file while capture is shadow stores a lesson the mode must not store."""

    async def body() -> None:
        judge = MemoryJudge(capture="yes")
        provider = ScriptedProvider([_assistant()])
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=judge,
            monkeypatch=monkeypatch,
            overlay={"memory.capture": "shadow"},
        )
        await asyncio.wait_for(_prompt(session, "Do not use tabs."), timeout=20)
        project = tmp_path / "project"
        assert _lessons(project) == []
        rows = _rows(project, "memory.capture")
        assert rows
        row = rows[0]
        assert row["mode"] == "shadow"
        assert row["action"] == "unchanged"
        assert "Do not use tabs." not in _ledger(project)

    asyncio.run(body())


def test_child_capture_does_not_write_a_file(tmp_path: Path) -> None:
    """Asking the judge, or appending a lesson, writes a file a child must not write."""
    from sillward.memory.capture import consider_user
    from sillward.memory.store import LessonStore

    judge = RaisingJudge()
    kernel, project = _kernel(tmp_path, judge, {"memory.capture": "active"})
    consider_user(kernel, LessonStore(project), "Do not commit secrets.", writer="child")
    assert not (project / ".sillward" / "lessons.jsonl").exists()
    row = _rows(project, "memory.capture")[0]
    assert row["action"] == "discard"
    assert row["answer"] is None
    assert "Do not commit secrets." not in _ledger(project)


def test_only_reusable_agent_lessons_are_stored(tmp_path: Path) -> None:
    """Storing a one-time, known, or child proposal keeps text it must drop."""
    from sillward.memory.capture import consider_agent
    from sillward.memory.store import LessonStore

    judge = MemoryJudge(worth=["reusable", "once", "known"], probability=0.88)
    kernel, project = _kernel(tmp_path, judge, {"memory.worth": "active"})
    store = LessonStore(project)
    consider_agent(kernel, store, "Ask before adding a dependency.", writer="parent")
    consider_agent(kernel, store, "This function is only for the November demo.", writer="parent")
    consider_agent(kernel, store, "The project uses pytest.", writer="parent")
    lessons = _lessons(project)
    assert len(lessons) == 1
    assert lessons[0]["body"] == "Ask before adding a dependency."
    assert lessons[0]["source"] == "agent"
    assert lessons[0]["probability"] == pytest.approx(0.88)

    child_kernel, child_project = _kernel(
        tmp_path / "child",
        RaisingJudge(),
        {"memory.worth": "active"},
    )
    consider_agent(
        child_kernel,
        LessonStore(child_project),
        "Ask before adding a dependency.",
        writer="child",
    )
    assert _lessons(child_project) == []
