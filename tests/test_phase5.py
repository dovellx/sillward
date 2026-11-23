"""Phase 5 exit checks: the comparison runner, its report, and one scored task.

A production change that drops a report field, scores a task without pytest,
lets a secret or the transcript into the report, or runs the nightly entry
without the Linux overlay fails these tests.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent

from eval.run import discover_tasks, load_config, main, nightly_plan, run_comparison, score_task
from sillward.protocol import Request, Verdict

REPO = Path(__file__).resolve().parents[1]
INSTRUCTION_MARK = "Implement add so it returns the arithmetic sum of the two integers."
TRANSCRIPT_MARK = "TRANSCRIPT-SHOULD-NOT-LEAK"
OPENAI_SECRET = "sk-phase5-openai-secret"
JEV_SECRET = "ts-phase5-jev-secret"
SOLUTION = "def add(left: int, right: int) -> int:\n    return left + right\n"
OVERLAY = {"tool.approval": "active", "turn.continue": "active"}
OVERLAY_JSON = json.dumps(OVERLAY, separators=(",", ":"), sort_keys=True)
REPORT_KEYS = {
    "blocked_delegations",
    "date",
    "mode_overlay",
    "model_id",
    "pass_rate",
    "sillward_version",
    "tasks",
    "tau_pin",
}
ARM_KEYS = {"blocked_delegations", "passed", "turns"}


def _verdict(answer: str, candidates: tuple[str, ...], probability: float = 0.95) -> Verdict:
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


class QuietJudge:
    """Fake judge. Answers are local; nothing is sent over the network."""

    name = "fake"

    def __init__(self, approval: float = 0.95) -> None:
        self.approval = approval

    def judge(self, request: Request) -> Verdict:
        if request.point == "tool.approval":
            return _verdict("allow", request.candidates, self.approval)
        if request.point == "tool.constraint":
            return _verdict("ok", request.candidates)
        if request.point == "task.frame":
            return _verdict("unchanged", request.candidates)
        if request.point == "turn.continue":
            return _verdict("stop", request.candidates)
        if request.point == "turn.completion":
            return _verdict("accept", request.candidates)
        if request.point == "tool.injection":
            return _verdict("keep", request.candidates)
        if request.point == "tool.admission":
            return _verdict("useful", request.candidates)
        if request.point == "memory.capture":
            answer = "no" if "no" in request.candidates else request.candidates[-1]
            return _verdict(answer, request.candidates)
        if request.point == "memory.worth":
            answer = "known" if "known" in request.candidates else request.candidates[-1]
            return _verdict(answer, request.candidates)
        if request.point == "swarm.patch":
            answer = "within" if "within" in request.candidates else request.candidates[0]
            return _verdict(answer, request.candidates)
        if request.point == "swarm.routing":
            return _verdict(request.candidates[0], request.candidates)
        if request.point == "memory.recall":
            answer = "skip" if "skip" in request.candidates else request.candidates[-1]
            return _verdict(answer, request.candidates)
        raise AssertionError(f"unexpected point {request.point}")


def _assistant(text: str = "", *calls: ToolCall) -> AssistantMessage:
    content: list[TextContent | ToolCall] = []
    if text:
        content.append(TextContent(text=text))
    content.extend(calls)
    return AssistantMessage(content=content, stop_reason="toolUse" if calls else "stop")


def _seen(system: str, messages: list[object], mark: str) -> bool:
    parts = [system]
    for message in messages:
        text = getattr(message, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return mark in "\n".join(parts)


class SolvingProvider:
    """Write the task solution, then stop. The stop text is a transcript marker."""

    def __init__(self) -> None:
        self.calls = 0
        self.saw_instruction = False

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
        del model, tools, signal, session_id
        self.calls += 1
        if _seen(system, messages, INSTRUCTION_MARK):
            self.saw_instruction = True
        if self.calls == 1:
            message = _assistant(
                "",
                ToolCall(
                    id="call-write",
                    name="write",
                    arguments={"path": "calc.py", "content": SOLUTION},
                ),
            )
        else:
            leaked = f"{TRANSCRIPT_MARK} {OPENAI_SECRET} {JEV_SECRET}"
            message = _assistant(leaked)
        yield AssistantDoneEvent(
            reason="toolUse" if message.tool_calls else "stop",
            message=message,
        )


class LoopingProvider:
    """Keep calling a tool. A second entry means the turn cap was not applied."""

    def __init__(self) -> None:
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
        if self.calls > 1:
            raise RuntimeError("turn limit was not applied")
        code = "print(1)"
        command = f'"{sys.executable}" -c "{code}"'
        message = _assistant(
            "",
            ToolCall(
                id="call-bash",
                name="bash",
                arguments={"command": command, "description": "Running a command"},
            ),
        )
        yield AssistantDoneEvent(reason="toolUse", message=message)


class HangingProvider:
    """Wait until the runner cancels the call."""

    def __init__(self) -> None:
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
        await asyncio.Event().wait()
        yield AssistantDoneEvent(reason="stop", message=_assistant("unused"))


class DelegatingProvider:
    """Ask for a child agent, then stop."""

    def __init__(self) -> None:
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
        if self.calls == 1:
            message = _assistant(
                "",
                ToolCall(
                    id="call-agent",
                    name="agent",
                    arguments={"role": "reviewer", "task": "look", "prefix": "src"},
                ),
            )
        else:
            message = _assistant("stopped")
        yield AssistantDoneEvent(
            reason="toolUse" if message.tool_calls else "stop",
            message=message,
        )


def _install_judge(monkeypatch: pytest.MonkeyPatch, judge: QuietJudge) -> None:
    def select(config: object, *, environ: object, tau_home: object, client: object) -> QuietJudge:
        del config, environ, tau_home, client
        return judge

    monkeypatch.setattr("sillward.judges.factory.select_judge", select)


def _isolate(monkeypatch: pytest.MonkeyPatch, home: Path, tau_home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    tau_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TAU_HOME", str(tau_home))
    monkeypatch.delenv("SILLWARD_MODE_OVERLAY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_SECRET)
    monkeypatch.setenv("TYPESAFE_API_KEY", JEV_SECRET)


def _report_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_eval_config_pins_model_turns_and_time_limit() -> None:
    """A drift in the pinned model, turn budget, or time limit changes the nightly arms."""

    config = load_config(REPO / "eval" / "config.toml")
    assert config.provider == "openai"
    assert config.model == "gpt-5.4"
    assert config.turns == 30
    assert config.timeout_seconds == 600


def test_sum_function_is_passed_or_failed_by_pytest(tmp_path: Path) -> None:
    """Treating a still-broken workspace as passed, or a solved one as failed, skips pytest."""

    tasks = discover_tasks(REPO / "eval" / "tasks")
    assert [task.id for task in tasks] == ["sum_function"]
    task = tasks[0]
    assert INSTRUCTION_MARK in task.instruction
    assert task.score_command == "python -m pytest hidden"

    untouched = tmp_path / "untouched"
    shutil.copytree(task.start, untouched)
    assert score_task(task, untouched) is False

    solved = tmp_path / "solved"
    shutil.copytree(task.start, solved)
    (solved / "calc.py").write_text(SOLUTION, encoding="utf-8")
    assert score_task(task, solved) is True


def test_fake_model_both_arms_fill_a_report_without_secrets_or_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing arm, a missing field, or a copied transcript makes the sample report unusable."""

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch, QuietJudge())
    providers: list[SolvingProvider] = []

    def factory(arm: str) -> SolvingProvider:
        del arm
        provider = SolvingProvider()
        providers.append(provider)
        return provider

    report_path = tmp_path / "report.json"
    work_root = tmp_path / "work"
    report = run_comparison(
        config_path=REPO / "eval" / "config.toml",
        tasks_dir=REPO / "eval" / "tasks",
        work_root=work_root,
        report_path=report_path,
        home=home,
        tau_home=tau_home,
        provider_factory=factory,
        now=datetime(2026, 11, 23, 23, 30, tzinfo=UTC),
    )
    written = json.loads(_report_text(report_path))
    assert written == report
    assert set(report) == REPORT_KEYS
    assert report["date"] == "2026-11-23"
    assert report["tau_pin"] == "0.4.8"
    assert report["sillward_version"] == "0.1.0"
    assert report["model_id"] == "gpt-5.4"
    assert report["mode_overlay"] == OVERLAY
    assert report["pass_rate"] == {"tau": 1.0, "sillward": 1.0}
    assert report["blocked_delegations"] == {"tau": 0, "sillward": 0}
    assert len(report["tasks"]) == 1
    task_row = report["tasks"][0]
    assert task_row["id"] == "sum_function"
    assert set(task_row) == {"id", "sillward", "tau"}
    for arm in ("tau", "sillward"):
        assert set(task_row[arm]) == ARM_KEYS
        assert task_row[arm]["passed"] is True
        assert task_row[arm]["turns"] == 2
        assert task_row[arm]["blocked_delegations"] == 0
    assert [provider.calls for provider in providers] == [2, 2]
    assert all(provider.saw_instruction for provider in providers)

    text = _report_text(report_path)
    assert OPENAI_SECRET not in text
    assert JEV_SECRET not in text
    assert TRANSCRIPT_MARK not in text
    assert INSTRUCTION_MARK not in text

    tau_workspace = work_root / "tau" / "sum_function"
    sillward_workspace = work_root / "sillward" / "sum_function"
    assert "return left + right" in (tau_workspace / "calc.py").read_text(encoding="utf-8")
    assert "return left + right" in (sillward_workspace / "calc.py").read_text(encoding="utf-8")
    assert not (tau_workspace / "hidden").exists()
    assert not (sillward_workspace / "hidden").exists()
    assert not (tau_workspace / ".sillward" / "ledger.jsonl").exists()
    ledger = [
        json.loads(line)
        for line in (sillward_workspace / ".sillward" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    continued = [row for row in ledger if row.get("point") == "turn.continue"]
    assert continued[-1]["mode"] == "active"


def test_configured_turn_limit_stops_both_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letting either arm take a second model call ignores the shared turn budget."""

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch, QuietJudge())
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'provider = "openai"\nmodel = "gpt-5.4"\nturns = 1\ntimeout_seconds = 600\n',
        encoding="utf-8",
    )
    providers: list[LoopingProvider] = []

    def factory(arm: str) -> LoopingProvider:
        del arm
        provider = LoopingProvider()
        providers.append(provider)
        return provider

    report = run_comparison(
        config_path=config_path,
        tasks_dir=REPO / "eval" / "tasks",
        work_root=tmp_path / "work",
        report_path=tmp_path / "report.json",
        home=home,
        tau_home=tau_home,
        provider_factory=factory,
        now=datetime(2026, 11, 23, 23, 30, tzinfo=UTC),
    )
    assert [provider.calls for provider in providers] == [1, 1]
    assert report["tasks"][0]["tau"]["turns"] == 1
    assert report["tasks"][0]["sillward"]["turns"] == 1


def test_configured_time_limit_still_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting out a hung model skips the time limit and never produces the sample report."""

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch, QuietJudge())
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'provider = "openai"\nmodel = "gpt-5.4"\nturns = 30\ntimeout_seconds = 1\n',
        encoding="utf-8",
    )

    def factory(arm: str) -> HangingProvider:
        del arm
        return HangingProvider()

    report_path = tmp_path / "report.json"
    report = run_comparison(
        config_path=config_path,
        tasks_dir=REPO / "eval" / "tasks",
        work_root=tmp_path / "work",
        report_path=report_path,
        home=home,
        tau_home=tau_home,
        provider_factory=factory,
        now=datetime(2026, 11, 23, 23, 30, tzinfo=UTC),
    )
    assert report_path.is_file()
    assert report["tasks"][0]["tau"]["passed"] is False
    assert report["tasks"][0]["sillward"]["passed"] is False
    assert report["tasks"][0]["tau"]["turns"] == 0
    assert report["tasks"][0]["sillward"]["turns"] == 0
    assert report["pass_rate"] == {"tau": 0.0, "sillward": 0.0}


def test_sillward_arm_counts_blocked_delegations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked child agent that is omitted from the report is easy to read as a broken role."""

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch, QuietJudge(approval=0.5))

    def factory(arm: str) -> DelegatingProvider:
        del arm
        return DelegatingProvider()

    report = run_comparison(
        config_path=REPO / "eval" / "config.toml",
        tasks_dir=REPO / "eval" / "tasks",
        work_root=tmp_path / "work",
        report_path=tmp_path / "report.json",
        home=home,
        tau_home=tau_home,
        provider_factory=factory,
        now=datetime(2026, 11, 23, 23, 30, tzinfo=UTC),
    )
    assert report["blocked_delegations"] == {"tau": 0, "sillward": 1}
    assert report["tasks"][0]["sillward"]["blocked_delegations"] == 1
    assert report["tasks"][0]["tau"]["blocked_delegations"] == 0
    assert report["tasks"][0]["sillward"]["passed"] is False


def test_nightly_entry_targets_linux_and_publishes_the_overlay(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nightly entry that is not Linux, or that omits the overlay, is not the comparison."""

    plan = nightly_plan()
    assert plan["platform"] == "linux"
    assert plan["overlay"] == OVERLAY

    assert main(["--plan"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["platform"] == "linux"
    assert printed["overlay"] == OVERLAY

    called: list[object] = []
    monkeypatch.setattr("eval.run.sys.platform", "win32")
    monkeypatch.setattr(
        "eval.run.run_comparison",
        lambda **kwargs: called.append(kwargs),
    )
    assert main([]) == 2
    assert called == []
    captured = capsys.readouterr()
    assert "Linux" in captured.err

    workflow = (REPO / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "ubuntu-latest" in workflow
    assert OVERLAY_JSON in workflow
    doc = (REPO / "docs" / "eval.md").read_text(encoding="utf-8")
    assert "Linux" in doc
    assert "SILLWARD_MODE_OVERLAY" in doc
    assert OVERLAY_JSON in doc
