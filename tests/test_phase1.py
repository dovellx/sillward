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


def _write_ignored_configs(home: Path, project: Path) -> None:
    """Plant config files a loader must not read."""
    project.mkdir(parents=True, exist_ok=True)
    payload = {
        "judge": "jev",
        "jev_model": "from-ignored-file",
        "llm_api_key": "sk-from-ignored-file",
        "modes": {"default": "active", "tool.approval": "active", "turn.continue": "off"},
        "thresholds": {"approval": 0.1, "approval_check": 0.1, "routing_override": 0.1},
        "record_state": True,
        "models": {
            "agent": {"provider": "ignored", "model": "from-ignored-file"},
            "judge": {"provider": "ignored", "model": "from-ignored-file"},
        },
    }
    text = json.dumps(payload)
    (project / "config.json").write_text(text, encoding="utf-8")
    (project / "sillward.json").write_text(text, encoding="utf-8")
    (project / ".sillward").mkdir(parents=True, exist_ok=True)
    (project / ".sillward" / "config-template.json").write_text(text, encoding="utf-8")
    destination = home / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(text, encoding="utf-8")


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
        config=load_config(project=project, environ={}),
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
        config=load_config(project=project, environ={}),
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
        config=load_config(project=project, environ={}),
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
        config=load_config(project=project, environ={}),
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


def test_home_and_loose_files_cannot_change_judge_or_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the home file, the template, or a loose file would adopt its judge and models."""
    from sillward.config import load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_ignored_configs(home, project)
    monkeypatch.chdir(project)

    config = load_config(project=project, environ={})

    assert config.judge == "llm"
    assert config.jev_model is None
    assert config.llm_api_key is None
    assert config.models.agent is None
    assert config.models.judge is None
    assert config.record_state is False
    assert config.thresholds.approval == 0.8
    assert config.thresholds.approval_check == 0.7
    assert config.thresholds.routing_override == 0.8
    assert {config.mode(point) for point in ALL_POINTS} == {"shadow"}


def test_user_config_is_read_from_the_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignoring .sillward/config.json keeps the shadow defaults and drops a non-default mode."""
    from sillward.config import load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_ignored_configs(home, project)
    monkeypatch.chdir(project)
    (project / ".sillward" / "config.json").write_text(
        json.dumps(
            {
                "judge": "jev",
                "jev_model": "system-one",
                "llm_api_key": "sk-from-project",
                "modes": {"default": "active", "tool.approval": "off"},
                "thresholds": {"approval": 0.55},
                "record_state": False,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(project=project, environ={})

    assert config.judge == "jev"
    assert config.jev_model == "system-one"
    assert config.llm_api_key == "sk-from-project"
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
    """Writing the overlay into the project config would change the next parse too."""
    from sillward.config import ConfigError, load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    _write_ignored_configs(home, project)
    monkeypatch.chdir(project)
    monkeypatch.delenv("SILLWARD_MODE_OVERLAY", raising=False)
    user_file = project / ".sillward" / "config.json"
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
    home_before = (home / ".sillward" / "config.json").read_text(encoding="utf-8")
    overlay = json.dumps({"tool.approval": "active", "turn.continue": "off"})

    seen = load_config(
        project=project,
        environ={"SILLWARD_MODE_OVERLAY": overlay, "TYPESAFE_API_KEY": API_KEY},
    )

    assert seen.judge == "jev"
    assert seen.mode("tool.approval") == "active"
    assert seen.mode("turn.continue") == "off"
    assert seen.mode("task.frame") == "shadow"
    assert user_file.read_text(encoding="utf-8") == before
    assert (home / ".sillward" / "config.json").read_text(encoding="utf-8") == home_before
    assert "SILLWARD_MODE_OVERLAY" not in os.environ
    again = load_config(project=project, environ={})
    assert again.mode("tool.approval") == "off"
    assert again.mode("turn.continue") == "shadow"
    assert again.judge == "jev"
    with pytest.raises(ConfigError):
        load_config(
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

    project = tmp_path / "project"
    project.mkdir()
    destination = project / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(
        json.dumps({"modes": {"task.frame": "off"}}),
        encoding="utf-8",
    )
    kernel = Kernel(
        config=load_config(project=project, environ={}),
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

    project = tmp_path / "project"
    project.mkdir()
    destination = project / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(
        json.dumps({"record_state": True}),
        encoding="utf-8",
    )
    Kernel(
        config=load_config(project=project, environ={}),
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


def _write_project_config(project: Path, payload: object) -> Path:
    destination = project / ".sillward"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _model_target(pair: tuple[str | None, str | None] | None):
    from sillward.config import ModelTarget

    if pair is None:
        return None
    return ModelTarget(provider=pair[0], model=pair[1])


@pytest.mark.parametrize(
    ("payload", "agent", "judge"),
    [
        pytest.param({}, None, None, id="absent"),
        pytest.param({"models": None}, None, None, id="null"),
        pytest.param({"models": {}}, None, None, id="empty"),
        pytest.param({"models": {"agent": None}}, None, None, id="null-agent"),
        pytest.param({"models": {"judge": None}}, None, None, id="null-judge"),
        pytest.param({"models": {"agent": {}}}, None, None, id="empty-agent"),
        pytest.param(
            {"models": {"judge": {"provider": None, "model": None, "api_key": None}}},
            None,
            None,
            id="null-fields",
        ),
        pytest.param(
            {
                "models": {
                    "agent": {"provider": "openai", "model": "gpt-5.4"},
                    "judge": {"provider": "openai", "model": "gpt-4.1"},
                }
            },
            ("openai", "gpt-5.4"),
            ("openai", "gpt-4.1"),
            id="both-fields",
        ),
        pytest.param(
            {"models": {"agent": {"model": "gpt-5.4"}, "judge": {"model": "gpt-4.1"}}},
            (None, "gpt-5.4"),
            (None, "gpt-4.1"),
            id="model-only",
        ),
        pytest.param(
            {"models": {"agent": {"provider": "openai"}, "judge": {"provider": "anthropic"}}},
            ("openai", None),
            ("anthropic", None),
            id="provider-only",
        ),
        pytest.param(
            {"models": {"agent": {"provider": "  openai  ", "model": " gpt-5.4 "}}},
            ("openai", "gpt-5.4"),
            None,
            id="stripped",
        ),
    ],
)
def test_project_file_names_agent_and_judge_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object, agent, judge
) -> None:
    """Leaving models unset would drop a model id the project file named."""
    from sillward.config import load_config

    project = tmp_path / "project"
    _write_project_config(project, payload)
    monkeypatch.chdir(project)

    config = load_config(project=project, environ={})

    assert config.models.agent == _model_target(agent)
    assert config.models.judge == _model_target(judge)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"models": "openai"}, "models must be an object"),
        ({"models": ["agent"]}, "models must be an object"),
        ({"models": {"explorer": {"model": "gpt-4o"}}}, "unknown model target explorer"),
        ({"models": {"agent": "gpt-5.4"}}, "models.agent must be an object"),
        ({"models": {"judge": ["gpt-4.1"]}}, "models.judge must be an object"),
        (
            {"models": {"agent": {"base_url": "https://example.test"}}},
            "unknown field models.agent.base_url",
        ),
        (
            {
                "llm_api_key": "sk-super-secret-sentinel",
                "models": {"agent": {"api_key": "   "}},
            },
            "models.agent.api_key must be a non-empty string",
        ),
        (
            {"models": {"judge": {"api_key": 1}}},
            "models.judge.api_key must be a non-empty string",
        ),
        (
            {"llm_api_key": "sk-super-secret-sentinel", "models": {"agent": {"provider": "   "}}},
            "models.agent.provider must be a non-empty string",
        ),
        ({"models": {"judge": {"model": ""}}}, "models.judge.model must be a non-empty string"),
        ({"models": {"agent": {"model": 5}}}, "models.agent.model must be a non-empty string"),
        (
            {"models": {"judge": {"provider": True}}},
            "models.judge.provider must be a non-empty string",
        ),
    ],
)
def test_malformed_models_raise_config_error(tmp_path: Path, payload: object, message: str) -> None:
    """Accepting these shapes would store a gateway, a blank id, or the raw key."""
    from sillward.config import ConfigError, load_config

    project = tmp_path / "project"
    _write_project_config(project, payload)

    with pytest.raises(ConfigError) as caught:
        load_config(project=project, environ={})

    assert str(caught.value) == message
    assert "sk-super-secret-sentinel" not in str(caught.value)
    assert "https://example.test" not in str(caught.value)


def test_mode_overlay_cannot_name_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An overlay model would change this process and could rewrite the project file."""
    from sillward.config import ConfigError, load_config

    project = tmp_path / "project"
    path = _write_project_config(project, {"modes": {"tool.approval": "off"}})
    monkeypatch.chdir(project)
    monkeypatch.delenv("SILLWARD_MODE_OVERLAY", raising=False)
    before = path.read_bytes()

    with pytest.raises(ConfigError, match="unknown point in SILLWARD_MODE_OVERLAY: models"):
        load_config(
            project=project,
            environ={
                "SILLWARD_MODE_OVERLAY": json.dumps({"models": {"agent": {"model": "gpt-4o"}}})
            },
        )

    assert path.read_bytes() == before


def test_model_api_keys_are_kept_and_hidden_from_repr(tmp_path: Path) -> None:
    """Dropping api_key would leave the coding session on tau's credential file."""
    from sillward.config import ModelTarget, load_config

    project = tmp_path / "project"
    _write_project_config(
        project,
        {
            "models": {
                "agent": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "api_key": "  sk-agent-sentinel  ",
                },
                "judge": {"api_key": "sk-judge-sentinel"},
            }
        },
    )

    config = load_config(project=project, environ={})

    assert config.models.agent == ModelTarget(
        provider="deepseek",
        model="deepseek-v4-pro",
        api_key="sk-agent-sentinel",
    )
    assert config.models.judge == ModelTarget(
        provider=None,
        model=None,
        api_key="sk-judge-sentinel",
    )
    rendered = repr(config)
    assert "sk-agent-sentinel" not in rendered
    assert "sk-judge-sentinel" not in rendered
