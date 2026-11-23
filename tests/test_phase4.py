"""Phase 4 exit checks: roles, sandbox, and shadow routing and patch.

A production change that offers reviewer or explorer anything besides read,
writes outside the prefix, leaves agent on a child session, reassigns a role
or rejects a patch while the point is shadow, or gives the three roles
different model ids fails these tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from tau_agent.messages import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.provider_events import AssistantDoneEvent
from tau_agent.session import InMemorySessionStorage
from tau_coding.extensions.api import NullUiBridge
from tau_coding.extensions.runtime import ExtensionRuntime
from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths
from tau_coding.session import CodingSession, CodingSessionConfig

import sillward.extension as extension_module
from sillward.protocol import Request, Verdict

REVIEW = "REVIEW-THE-DIFF"
FIND = "FIND-THE-TARGET"
IMPLEMENT = "IMPLEMENT-THE-CHANGE"
MODEL_ID = "shared-model"


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


class ScriptJudge:
    """Fake judge. Answers come from the test, never from the network."""

    name = "fake"

    def __init__(
        self,
        *,
        routing: str | None = None,
        routing_probability: float = 0.95,
        patch: str = "beyond",
        approval: tuple[str, float] = ("allow", 0.95),
    ) -> None:
        self.routing = routing
        self.routing_probability = routing_probability
        self.patch = patch
        self.approval = approval
        self.requests: list[Request] = []

    def judge(self, request: Request) -> Verdict:
        self.requests.append(request)
        if request.point == "swarm.routing":
            requested = request.state.get("requested")
            answer = self.routing
            if answer is None and isinstance(requested, str) and requested in request.candidates:
                answer = requested
            if answer is None or answer not in request.candidates:
                answer = request.candidates[0]
            return _verdict(answer, request.candidates, self.routing_probability)
        if request.point == "swarm.patch":
            answer = self.patch if self.patch in request.candidates else request.candidates[-1]
            return _verdict(answer, request.candidates, 0.91)
        if request.point == "tool.approval":
            return _verdict(self.approval[0], request.candidates, self.approval[1])
        if request.point == "tool.constraint":
            return _verdict("ok", request.candidates)
        if request.point == "task.frame":
            return _verdict("unchanged", request.candidates)
        if request.point == "turn.continue":
            return _verdict("stop", request.candidates)
        if request.point == "turn.completion":
            return _verdict("accept", request.candidates)
        if request.point == "memory.capture":
            return _verdict("no", request.candidates)
        if request.point == "memory.worth":
            return _verdict("known", request.candidates)
        if request.point == "memory.recall":
            body = str(request.state.get("body", ""))
            if "KEEP-RULE" in body and "keep-me" in request.candidates:
                return _verdict("keep-me", request.candidates)
            return _verdict("skip", request.candidates)
        if request.point == "tool.injection":
            return _verdict("keep", request.candidates)
        if request.point == "tool.admission":
            return _verdict("useful", request.candidates)
        raise AssertionError(f"unexpected point {request.point}")


class RecordingProvider:
    """Fake model. Records every call, then replays the next scripted message."""

    def __init__(self, messages: list[AssistantMessage]) -> None:
        self._messages = messages
        self.calls: list[dict[str, object]] = []

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
        del signal, session_id
        self.calls.append(
            {
                "model": model,
                "system": system,
                "tools": [str(getattr(tool, "name", tool)) for tool in tools],
                "users": [message.text for message in messages if isinstance(message, UserMessage)],
                "messages": list(messages),
            }
        )
        if len(self.calls) > len(self._messages):
            raise RuntimeError(f"provider called {len(self.calls)} times")
        yield AssistantDoneEvent(
            reason="toolUse" if self._messages[len(self.calls) - 1].tool_calls else "stop",
            message=self._messages[len(self.calls) - 1].model_copy(deep=True),
        )


class HangingUi(NullUiBridge):
    """Non-interactive bridge. A confirm or input call waits forever."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def has_ui(self) -> bool:
        return False

    async def confirm(self, title: str, message: str, *, timeout: float | None = None) -> bool:
        del title, message, timeout
        self.calls += 1
        await asyncio.Event().wait()
        return False


def _assistant(text: str = "", *calls: ToolCall) -> AssistantMessage:
    content: list[TextContent | ToolCall] = []
    if text:
        content.append(TextContent(text=text))
    content.extend(calls)
    return AssistantMessage(content=content, stop_reason="toolUse" if calls else "stop")


def _agent(role: str, task: str, prefix: str, call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="agent",
        arguments={"role": role, "task": task, "prefix": prefix},
    )


def _write(path: str, content: str, call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="write",
        arguments={"path": path, "content": content},
    )


def _edit(path: str, old: str, new: str, call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="edit",
        arguments={"path": path, "edits": [{"oldText": old, "newText": new}]},
    )


async def _open_session(
    tmp: Path,
    *,
    provider: RecordingProvider,
    judge: object,
    monkeypatch: pytest.MonkeyPatch,
    overlay: Mapping[str, str],
    ui: NullUiBridge | None = None,
    model: str = MODEL_ID,
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
    previous = None
    if ui is not None:
        previous = ExtensionRuntime(ui=ui, paths=paths, built_in_extensions=())
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model=model,
            thinking_level="high",
            storage=InMemorySessionStorage(),
            cwd=project,
            system="phase 4 test session",
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
            extension_runtime=previous,
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


def _ledger(project: Path) -> list[dict[str, object]]:
    path = project / ".sillward" / "ledger.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _rows(project: Path, point: str) -> list[dict[str, object]]:
    return [row for row in _ledger(project) if row.get("point") == point]


def _tool_texts(session: CodingSession, name: str) -> list[str]:
    return [
        message.text
        for message in session.messages
        if isinstance(message, ToolResultMessage) and message.tool_name == name
    ]


def _calls_for(provider: RecordingProvider, marker: str) -> list[dict[str, object]]:
    return [
        call
        for call in provider.calls
        if any(marker in text for text in call["users"])  # type: ignore[union-attr]
    ]


def _result_texts(call: dict[str, object], name: str) -> list[str]:
    return [
        message.text
        for message in call["messages"]  # type: ignore[union-attr]
        if isinstance(message, ToolResultMessage) and message.tool_name == name
    ]


def _three_script() -> list[AssistantMessage]:
    return [
        _assistant("", _agent("reviewer", REVIEW, "src", "agent-review")),
        _assistant("reviewer-done"),
        _assistant("", _agent("explorer", FIND, "src", "agent-explore")),
        _assistant("explorer-done"),
        _assistant("", _agent("implementer", IMPLEMENT, "src", "agent-implement")),
        _assistant("implementer-done"),
        _assistant("parent-done"),
    ]


async def _run(
    tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    *,
    routing: str | None = None,
    routing_probability: float = 0.95,
    patch: str = "beyond",
    overlay: Mapping[str, str] | None = None,
) -> CodingSession:
    session = await _open_session(
        tmp,
        provider=provider,
        judge=ScriptJudge(
            routing=routing,
            routing_probability=routing_probability,
            patch=patch,
        ),
        monkeypatch=monkeypatch,
        overlay={} if overlay is None else overlay,
    )
    await asyncio.wait_for(_prompt(session, "delegate the work"), timeout=60)
    return session


def test_reviewer_and_explorer_are_offered_only_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Giving either role write, edit, bash, or agent puts that name on the child call."""

    async def body() -> None:
        provider = RecordingProvider(_three_script())
        await _run(tmp_path, monkeypatch, provider)
        reviewer = _calls_for(provider, REVIEW)
        explorer = _calls_for(provider, FIND)
        assert reviewer
        assert explorer
        assert reviewer[0]["tools"] == ["read"]
        assert explorer[0]["tools"] == ["read"]
        assert "## reviewer\n" in str(reviewer[0]["system"])
        assert "## explorer\n" in str(explorer[0]["system"])

    asyncio.run(body())


def test_child_sessions_are_not_offered_the_agent_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering agent on the child puts that name on a role's tool list."""

    async def body() -> None:
        provider = RecordingProvider(_three_script())
        await _run(tmp_path, monkeypatch, provider)
        for marker in (REVIEW, FIND, IMPLEMENT):
            calls = _calls_for(provider, marker)
            assert calls, marker
            assert "agent" not in calls[0]["tools"]
        implementer = _calls_for(provider, IMPLEMENT)[0]["tools"]
        assert implementer == ["read", "write", "edit", "bash"]

    asyncio.run(body())


def test_role_sessions_use_the_same_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child created with another model id shows that id on its provider call."""

    async def body() -> None:
        provider = RecordingProvider(_three_script())
        await _run(tmp_path, monkeypatch, provider)
        grouped = [_calls_for(provider, marker) for marker in (REVIEW, FIND, IMPLEMENT)]
        assert all(grouped)
        models = [group[0]["model"] for group in grouped]
        assert models == [MODEL_ID, MODEL_ID, MODEL_ID]

    asyncio.run(body())


def test_implementer_write_outside_the_prefix_fails_before_the_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that runs first creates the file or updates the outside directory."""

    async def body() -> None:
        project = tmp_path / "project"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(parents=True)
        (project / "src").mkdir(parents=True, exist_ok=True)
        target = elsewhere / "new.txt"
        before = elsewhere.stat().st_mtime_ns
        provider = RecordingProvider(
            [
                _assistant("", _agent("implementer", "WRITE-OUTSIDE", "src", "agent-write")),
                _assistant("", _write("src/../../elsewhere/new.txt", "pwned", "write-out")),
                _assistant("write-stopped"),
                _assistant("parent-done"),
            ]
        )
        await _run(tmp_path, monkeypatch, provider)
        calls = _calls_for(provider, "WRITE-OUTSIDE")
        assert len(calls) >= 2
        results = _result_texts(calls[1], "write")
        assert results
        assert all("Successfully wrote" not in text for text in results)
        assert not target.exists()
        assert elsewhere.stat().st_mtime_ns == before

    asyncio.run(body())


def test_implementer_edit_outside_the_prefix_fails_before_the_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edit that runs first changes the bytes or the modification time."""

    async def body() -> None:
        project = tmp_path / "project"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(parents=True)
        (project / "src").mkdir(parents=True, exist_ok=True)
        target = elsewhere / "secret.txt"
        target.write_text("keep\n", encoding="utf-8")
        before_bytes = target.read_bytes()
        before_mtime = target.stat().st_mtime_ns
        provider = RecordingProvider(
            [
                _assistant("", _agent("implementer", "EDIT-OUTSIDE", "src", "agent-edit")),
                _assistant(
                    "",
                    _edit("src/../../elsewhere/secret.txt", "keep", "changed", "edit-out"),
                ),
                _assistant("edit-stopped"),
                _assistant("parent-done"),
            ]
        )
        await _run(tmp_path, monkeypatch, provider)
        calls = _calls_for(provider, "EDIT-OUTSIDE")
        assert len(calls) >= 2
        results = _result_texts(calls[1], "edit")
        assert results
        assert all("Successfully replaced" not in text for text in results)
        assert target.read_bytes() == before_bytes
        assert target.stat().st_mtime_ns == before_mtime

    asyncio.run(body())


def test_shadow_routing_keeps_the_requested_role_and_records_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reassigning to implementer offers write, and skipping the ask leaves no ledger row."""

    async def body() -> None:
        provider = RecordingProvider(
            [
                _assistant("", _agent("reviewer", "REVIEW-THIS-DIFF", "src", "agent-route")),
                _assistant("", _write("src/routed.txt", "should-not", "write-route")),
                _assistant("reviewer-stopped"),
                _assistant("parent-done"),
            ]
        )
        await _run(tmp_path, monkeypatch, provider, routing="implementer")
        calls = _calls_for(provider, "REVIEW-THIS-DIFF")
        assert calls
        assert calls[0]["tools"] == ["read"]
        assert not (tmp_path / "project" / "src" / "routed.txt").exists()
        row = _rows(tmp_path / "project", "swarm.routing")[0]
        assert row["answer"] == "implementer"
        assert row["mode"] == "shadow"
        assert row["action"] == "unchanged"
        assert row["probabilities"]["implementer"] == pytest.approx(0.95)
        assert "REVIEW-THIS-DIFF" not in json.dumps(row)

    asyncio.run(body())


def _routing_script() -> list[AssistantMessage]:
    return [
        _assistant("", _agent("reviewer", "REVIEW-THIS-DIFF", "src", "agent-route")),
        _assistant("", _write("src/routed.txt", "should-not", "write-route")),
        _assistant("reviewer-stopped"),
        _assistant("parent-done"),
    ]


def test_active_routing_reassigns_at_the_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staying with reviewer leaves write off the child and the file unwritten."""

    async def body() -> None:
        provider = RecordingProvider(_routing_script())
        await _run(
            tmp_path,
            monkeypatch,
            provider,
            routing="implementer",
            routing_probability=0.80,
            overlay={"swarm.routing": "active"},
        )
        calls = _calls_for(provider, "REVIEW-THIS-DIFF")
        assert calls
        assert calls[0]["tools"] == ["read", "write", "edit", "bash"]
        assert (tmp_path / "project" / "src" / "routed.txt").read_text(encoding="utf-8") == (
            "should-not"
        )
        row = _rows(tmp_path / "project", "swarm.routing")[0]
        assert row["answer"] == "implementer"
        assert row["mode"] == "active"
        assert row["action"] == "execute"
        assert row["probabilities"]["implementer"] == pytest.approx(0.80)

    asyncio.run(body())


def test_active_routing_keeps_the_role_below_the_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reassigning below 0.80 offers write and creates the file."""

    async def body() -> None:
        provider = RecordingProvider(_routing_script())
        await _run(
            tmp_path,
            monkeypatch,
            provider,
            routing="implementer",
            routing_probability=0.79,
            overlay={"swarm.routing": "active"},
        )
        calls = _calls_for(provider, "REVIEW-THIS-DIFF")
        assert calls
        assert calls[0]["tools"] == ["read"]
        assert not (tmp_path / "project" / "src" / "routed.txt").exists()
        row = _rows(tmp_path / "project", "swarm.routing")[0]
        assert row["answer"] == "implementer"
        assert row["mode"] == "active"
        assert row["action"] == "unchanged"
        assert row["probabilities"]["implementer"] == pytest.approx(0.79)

    asyncio.run(body())


def test_shadow_patch_keeps_the_change_and_records_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejecting the patch removes the file or the implementer's closing text."""

    async def body() -> None:
        project = tmp_path / "project"
        (project / "src").mkdir(parents=True, exist_ok=True)
        provider = RecordingProvider(
            [
                _assistant("", _agent("implementer", "IMPLEMENT-INSIDE", "src", "agent-patch")),
                _assistant("", _write("src/inside.txt", "landed", "write-in")),
                _assistant("patch-landed"),
                _assistant("parent-done"),
            ]
        )
        session = await _run(tmp_path, monkeypatch, provider, patch="beyond")
        written = project / "src" / "inside.txt"
        assert written.read_text(encoding="utf-8") == "landed"
        assert any("patch-landed" in text for text in _tool_texts(session, "agent"))
        row = _rows(project, "swarm.patch")[0]
        assert row["answer"] == "beyond"
        assert row["mode"] == "shadow"
        assert row["action"] == "unchanged"
        assert "landed" not in json.dumps(row)
        assert "IMPLEMENT-INSIDE" not in json.dumps(row)

    asyncio.run(body())


def test_spawn_below_the_approval_threshold_is_blocked_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letting the child start, or asking a person who is not there, misses the block."""

    async def body() -> None:
        ui = HangingUi()
        provider = RecordingProvider(
            [
                _assistant("", _agent("implementer", "APPROVE-SPAWN", "src", "agent-approve")),
                _assistant("parent-done"),
            ]
        )
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=ScriptJudge(approval=("allow", 0.79)),
            monkeypatch=monkeypatch,
            overlay={"tool.approval": "active", "tool.constraint": "off"},
            ui=ui,
        )
        await asyncio.wait_for(_prompt(session, "delegate the work"), timeout=20)
        texts = _tool_texts(session, "agent")
        assert texts
        assert "Tool call blocked" in texts[0]
        assert ui.calls == 0
        assert len(provider.calls) == 2
        assert not _calls_for(provider, "APPROVE-SPAWN")
        row = _rows(tmp_path / "project", "tool.approval")[0]
        assert row["action"] == "block"
        assert row["mode"] == "active"
        assert row["probabilities"]["allow"] == pytest.approx(0.79)

    asyncio.run(body())


def test_active_recall_puts_the_selected_lesson_into_the_child_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping recall leaves the child task without the selected lesson."""

    async def body() -> None:
        provider = RecordingProvider(
            [
                _assistant("", _agent("implementer", "IMPLEMENT-WITH-LESSONS", "src", "agent-mem")),
                _assistant("memory-done"),
                _assistant("parent-done"),
            ]
        )
        session = await _open_session(
            tmp_path,
            provider=provider,
            judge=ScriptJudge(),
            monkeypatch=monkeypatch,
            overlay={"memory.recall": "active"},
        )
        lessons = tmp_path / "project" / ".sillward" / "lessons.jsonl"
        lessons.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "id": "keep-me",
                "body": "KEEP-RULE: use the helper",
                "source": "user",
                "created_at": "2026-01-01T00:00:00+00:00",
                "probability": 0.95,
            },
            {
                "id": "drop-me",
                "body": "DROP-RULE: ignore the helper",
                "source": "user",
                "created_at": "2026-01-01T00:00:00+00:00",
                "probability": 0.95,
            },
        ]
        lessons.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        await asyncio.wait_for(_prompt(session, "delegate the work"), timeout=60)
        calls = _calls_for(provider, "IMPLEMENT-WITH-LESSONS")
        assert calls
        text = "\n".join(calls[0]["users"])  # type: ignore[arg-type]
        assert "KEEP-RULE: use the helper" in text
        assert "DROP-RULE" not in text
        assert "IMPLEMENT-WITH-LESSONS" in text

    asyncio.run(body())
