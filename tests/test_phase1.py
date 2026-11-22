"""Phase 1 exit checks: protocol, config, fake judge, and ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ORIGINAL = "user-original-do-not-touch-the-database"
PARAMETERS = "bash-param-rm-rf-sentinel"
FILE_CONTENT = "file-body-secret-sentinel"
API_KEY = "sk-super-secret-sentinel"

BLOCKING = frozenset({"tool.approval", "tool.constraint"})
ALL_POINTS = (
    "task.frame",
    "tool.approval",
    "tool.constraint",
    "tool.injection",
    "tool.admission",
    "turn.continue",
    "turn.completion",
    "memory.recall",
    "memory.capture",
    "memory.worth",
    "swarm.routing",
    "swarm.patch",
)
FAILURES = ("timeout", "http", "answer-outside-candidates", "missing-probability")


def _request(point: str, *, summary: str = ORIGINAL):
    from sillward.protocol import Question, Request

    return Request(
        point=point,
        question=Question.CHOICE,
        candidates=("ship", "hold"),
        frame_summary=summary,
        state={
            "parameters": PARAMETERS,
            "file_content": FILE_CONTENT,
            "step": "compile",
        },
    )


def _judge(kind: str):
    from sillward.judges.base import JudgeFailure
    from sillward.judges.fake import FakeJudge

    if kind == "timeout":
        return FakeJudge(failure=JudgeFailure(f"timeout {API_KEY} {FILE_CONTENT}"))
    if kind == "http":
        return FakeJudge(failure=JudgeFailure(f"http 503 {API_KEY} {FILE_CONTENT}"))
    if kind == "answer-outside-candidates":
        return FakeJudge(
            answer="not-a-candidate",
            probabilities={"ship": 0.4, "hold": 0.6},
            elapsed_ms=4.0,
        )
    if kind == "missing-probability":
        return FakeJudge(answer="ship", probabilities={"ship": 1.0}, elapsed_ms=4.0)
    raise AssertionError(kind)


def _write_project_configs(project: Path) -> None:
    """Plant config files a loader must not read."""
    nested = project / ".sillward"
    nested.mkdir(parents=True)
    payload = {
        "judge": "jev",
        "jev_model": "from-project",
        "modes": {"default": "active", "tool.approval": "active", "turn.continue": "off"},
        "thresholds": {"approval": 0.1, "approval_check": 0.1, "routing_override": 0.1},
        "record_state": True,
    }
    text = json.dumps(payload)
    (nested / "config.json").write_text(text, encoding="utf-8")
    (project / "config.json").write_text(text, encoding="utf-8")
    (project / "sillward.json").write_text(text, encoding="utf-8")


def _ledger_text(project: Path) -> str:
    return (project / ".sillward" / "ledger.jsonl").read_text(encoding="utf-8")


def _ledger_row(project: Path) -> dict[str, object]:
    lines = _ledger_text(project).splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert isinstance(row, dict)
    return row


def test_declared_points_carry_a_default_mode_and_a_failure_class() -> None:
    """Adding a point without a default mode or a block/off class fails this list."""
    from sillward.kernel.points import POINTS

    assert tuple(POINTS) == ALL_POINTS
    assert {point_id for point_id, spec in POINTS.items() if spec.blocks_tool} == set(BLOCKING)
    assert {spec.default_mode for spec in POINTS.values()} == {"shadow"}


def test_fake_judge_answer_is_written_to_the_ledger(tmp_path: Path) -> None:
    """Dropping the verdict answer leaves the ledger without the injected choice."""
    from sillward.config import load_config
    from sillward.judges.fake import FakeJudge
    from sillward.kernel.runner import Kernel

    project = tmp_path / "project"
    project.mkdir()
    judge = FakeJudge(answer="ship", probabilities={"ship": 0.81, "hold": 0.19}, elapsed_ms=12.5)
    kernel = Kernel(
        config=load_config(home=tmp_path / "home", project=project, environ={}),
        judge=judge,
        project=project,
    )

    decision = kernel.consult(_request("task.frame"))

    row = _ledger_row(project)
    assert decision.answer == "ship"
    assert row["answer"] == "ship"
    assert row["probabilities"] == {"ship": 0.81, "hold": 0.19}
    assert row["elapsed_ms"] == 12.5
    assert row["adapter"] == "fake"
    assert row["point"] == "task.frame"
    assert row["mode"] == "shadow"


def test_default_ledger_row_omits_source_text_parameters_and_file_content(tmp_path: Path) -> None:
    """Serializing the request, or the judge's error text, copies those sentinels into the row."""
    from sillward.config import load_config
    from sillward.judges.fake import FakeJudge
    from sillward.kernel.runner import Kernel

    project = tmp_path / "project"
    project.mkdir()
    kernel = Kernel(
        config=load_config(home=tmp_path / "home", project=project, environ={}),
        judge=FakeJudge(
            answer="ship",
            probabilities={"ship": 0.81, "hold": 0.19},
            elapsed_ms=12.5,
        ),
        project=project,
    )

    kernel.consult(_request("tool.injection"))

    text = _ledger_text(project)
    row = json.loads(text)
    assert set(row) == {
        "point",
        "answer",
        "probabilities",
        "elapsed_ms",
        "mode",
        "adapter",
        "action",
    }
    assert ORIGINAL not in text
    assert PARAMETERS not in text
    assert FILE_CONTENT not in text
    assert "request" not in row


@pytest.mark.parametrize("point", [point for point in ALL_POINTS if point in BLOCKING])
@pytest.mark.parametrize("kind", FAILURES)
def test_blocking_points_block_when_the_judge_fails(tmp_path: Path, point: str, kind: str) -> None:
    """A timeout, 5xx, or out-of-protocol verdict must block these calls, not run them."""
    from sillward.config import load_config
    from sillward.kernel.runner import Kernel

    project = tmp_path / "project"
    project.mkdir()
    decision = Kernel(
        config=load_config(home=tmp_path / "home", project=project, environ={}),
        judge=_judge(kind),
        project=project,
    ).consult(_request(point))

    row = _ledger_row(project)
    text = _ledger_text(project)
    assert decision.action == "block"
    assert decision.answer is None
    assert row["action"] == "block"
    assert row["answer"] is None
    assert row["probabilities"] is None
    assert row["adapter"] == "fake"
    assert row["mode"] == "shadow"
    assert API_KEY not in text
    assert FILE_CONTENT not in text
    assert PARAMETERS not in text
    assert ORIGINAL not in text


@pytest.mark.parametrize("point", [point for point in ALL_POINTS if point not in BLOCKING])
@pytest.mark.parametrize("kind", FAILURES)
def test_other_points_are_treated_as_off_when_the_judge_fails(
    tmp_path: Path, point: str, kind: str
) -> None:
    """The same judge failure must not block a point that only observes."""
    from sillward.config import load_config
    from sillward.kernel.runner import Kernel

    project = tmp_path / "project"
    project.mkdir()
    decision = Kernel(
        config=load_config(home=tmp_path / "home", project=project, environ={}),
        judge=_judge(kind),
        project=project,
    ).consult(_request(point))

    row = _ledger_row(project)
    assert decision.action == "off"
    assert decision.answer is None
    assert row["action"] == "off"
    assert row["answer"] is None
    assert row["adapter"] == "fake"
    assert row["point"] == point


def test_project_directory_config_cannot_change_judge_or_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a project file would adopt its judge and active modes."""
    from sillward.config import load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_project_configs(project)
    monkeypatch.chdir(project)

    config = load_config(home=home, project=project, environ={})

    assert config.judge == "llm"
    assert config.jev_model is None
    assert config.record_state is False
    assert config.thresholds.approval == 0.8
    assert config.thresholds.approval_check == 0.7
    assert config.thresholds.routing_override == 0.8
    assert {config.mode(point) for point in ALL_POINTS} == {"shadow"}


def test_user_config_is_read_from_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignoring the home file keeps the shadow defaults and drops a non-default mode."""
    from sillward.config import load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_project_configs(project)
    monkeypatch.chdir(project)
    destination = home / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(
        json.dumps(
            {
                "judge": "jev",
                "jev_model": "system-one",
                "modes": {"default": "active", "tool.approval": "off"},
                "thresholds": {"approval": 0.55},
                "record_state": False,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(home=home, project=project, environ={})

    assert config.judge == "jev"
    assert config.jev_model == "system-one"
    assert config.mode("tool.approval") == "off"
    assert config.mode("task.frame") == "active"
    assert config.mode("turn.continue") == "active"
    assert config.thresholds.approval == 0.55
    assert config.thresholds.approval_check == 0.7
    assert config.thresholds.routing_override == 0.8
    assert config.record_state is False


def test_mode_overlay_changes_only_the_parse_that_receives_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing the overlay into the home file would change the next parse too."""
    from sillward.config import ConfigError, load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_project_configs(project)
    monkeypatch.chdir(project)
    monkeypatch.delenv("SILLWARD_MODE_OVERLAY", raising=False)
    destination = home / ".sillward"
    destination.mkdir(parents=True)
    user_file = destination / "config.json"
    user_file.write_text(
        json.dumps(
            {
                "judge": "jev",
                "jev_model": "system-one",
                "modes": {"default": "shadow", "tool.approval": "off"},
            }
        ),
        encoding="utf-8",
    )
    before = user_file.read_text(encoding="utf-8")
    overlay = json.dumps({"tool.approval": "active", "turn.continue": "off"})

    seen = load_config(
        home=home,
        project=project,
        environ={"SILLWARD_MODE_OVERLAY": overlay, "TYPESAFE_API_KEY": API_KEY},
    )

    assert seen.judge == "jev"
    assert seen.mode("tool.approval") == "active"
    assert seen.mode("turn.continue") == "off"
    assert seen.mode("task.frame") == "shadow"
    assert user_file.read_text(encoding="utf-8") == before
    assert "SILLWARD_MODE_OVERLAY" not in os.environ
    again = load_config(home=home, project=project, environ={})
    assert again.mode("tool.approval") == "off"
    assert again.mode("turn.continue") == "shadow"
    assert again.judge == "jev"
    with pytest.raises(ConfigError):
        load_config(
            home=home,
            project=project,
            environ={"SILLWARD_MODE_OVERLAY": json.dumps({"judge": "llm"})},
        )
    assert user_file.read_text(encoding="utf-8") == before


def test_off_mode_does_not_ask_the_judge(tmp_path: Path) -> None:
    """Calling the judge in off mode would store the injected answer."""
    from sillward.config import load_config
    from sillward.kernel.runner import Kernel

    class _CallsJudge:
        name = "fake"

        def judge(self, request: object) -> object:
            raise AssertionError(f"off mode asked the judge about {request!r}")

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    destination = home / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(
        json.dumps({"modes": {"task.frame": "off"}}),
        encoding="utf-8",
    )
    kernel = Kernel(
        config=load_config(home=home, project=project, environ={}),
        judge=_CallsJudge(),
        project=project,
    )

    decision = kernel.consult(_request("task.frame"))

    row = _ledger_row(project)
    assert decision.action == "off"
    assert decision.answer is None
    assert row["answer"] is None
    assert row["adapter"] is None
    assert row["action"] == "off"
    assert row["mode"] == "off"
    assert "ship" not in _ledger_text(project)


def test_recorded_state_redacts_parameters_and_file_content(tmp_path: Path) -> None:
    """record_state true would otherwise append the raw tool arguments and file body."""
    from sillward.config import load_config
    from sillward.judges.fake import FakeJudge
    from sillward.kernel.runner import Kernel

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    destination = home / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(
        json.dumps({"record_state": True}),
        encoding="utf-8",
    )
    Kernel(
        config=load_config(home=home, project=project, environ={}),
        judge=FakeJudge(answer="ship", probabilities={"ship": 0.81, "hold": 0.19}, elapsed_ms=1),
        project=project,
    ).consult(_request("task.frame", summary="short-summary"))

    row = _ledger_row(project)
    recorded = row["request"]
    assert isinstance(recorded, dict)
    assert recorded["point"] == "task.frame"
    assert recorded["frame_summary"] == "short-summary"
    assert recorded["candidates"] == ["ship", "hold"]
    state = recorded["state"]
    assert isinstance(state, dict)
    assert state["parameters"] == "[redacted]"
    assert state["file_content"] == "[redacted]"
    assert state["step"] == "compile"
    text = _ledger_text(project)
    assert PARAMETERS not in text
    assert FILE_CONTENT not in text
