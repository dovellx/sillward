"""Phase 6 exit checks: archive the comparison as an acceptance record.

A production change that drops a number from the record, fails the process
because sillward scored below tau, promotes more than one shadow point, or
skips a task fails these tests.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent

from eval.acceptance import AcceptanceError, acceptance_exit_code, write_acceptance
from eval.run import acceptance_root, discover_tasks, main, nightly_plan, run_comparison
from sillward.protocol import Request, Verdict

REPO = Path(__file__).resolve().parents[1]
WHEN = datetime(2026, 11, 23, 23, 30, tzinfo=UTC)
DATE = "2026-11-23"
BASE_OVERLAY = {"tool.approval": "active", "turn.continue": "active"}
CONFIG_TEXT = '{"judge": "llm"}\n'
OPENAI_SECRET = "sk-phase6-openai-secret"
JEV_SECRET = "ts-phase6-jev-secret"
TRANSCRIPT_MARK = "PHASE6-TRANSCRIPT-SHOULD-NOT-LEAK"
SOLUTION = "def add(left: int, right: int) -> int:\n    return left + right\n"
INSTRUCTION_MARK = "Implement add so it returns the arithmetic sum of the two integers."


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
    """Fake judge. Answers stay in process."""

    name = "fake"

    def judge(self, request: Request) -> Verdict:
        if request.point == "tool.approval":
            return _verdict("allow", request.candidates)
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


class SolvingProvider:
    """Write the sum_function solution, then stop. The stop text is not a report field."""

    def __init__(self) -> None:
        self.calls = 0
        self.models: list[str] = []

    async def aclose(self) -> None:
        return None

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
        del system, messages, tools, signal, session_id
        self.calls += 1
        self.models.append(model)
        if self.calls % 2 == 1:
            message = _assistant(
                "",
                ToolCall(
                    id=f"call-write-{self.calls}",
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


class IdleProvider:
    """Stop without editing the workspace."""

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
        yield AssistantDoneEvent(reason="stop", message=_assistant("not solved"))


def _install_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    def select(config: object, *, environ: object, tau_home: object, client: object) -> QuietJudge:
        del config, environ, tau_home, client
        return QuietJudge()

    monkeypatch.setattr("sillward.judges.factory.select_judge", select)


def _isolate(monkeypatch: pytest.MonkeyPatch, home: Path, tau_home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    tau_home.mkdir(parents=True, exist_ok=True)
    config = home / ".sillward" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TAU_HOME", str(tau_home))
    monkeypatch.delenv("SILLWARD_MODE_OVERLAY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_SECRET)
    monkeypatch.setenv("TYPESAFE_API_KEY", JEV_SECRET)


def _sample_report() -> dict[str, object]:
    return {
        "blocked_delegations": {"sillward": 2, "tau": 0},
        "date": DATE,
        "mode_overlay": dict(BASE_OVERLAY),
        "model_id": "gpt-5.4",
        "pass_rate": {"sillward": 0.0, "tau": 0.5},
        "sillward_version": "0.1.0",
        "tasks": [
            {
                "id": "alpha",
                "sillward": {"blocked_delegations": 2, "passed": False, "turns": 3},
                "tau": {"blocked_delegations": 0, "passed": True, "turns": 4},
            },
            {
                "id": "beta",
                "sillward": {"blocked_delegations": 0, "passed": False, "turns": 1},
                "tau": {"blocked_delegations": 0, "passed": False, "turns": 2},
            },
        ],
        "tau_pin": "0.4.8",
        "transcript": TRANSCRIPT_MARK,
    }


def _write_report_file(path: Path, report: dict[str, object]) -> None:
    stored = {key: value for key, value in report.items() if key != "transcript"}
    path.write_text(json.dumps(stored), encoding="utf-8")


def _record(bundle: Path) -> dict[str, object]:
    return json.loads((bundle / "record.json").read_text(encoding="utf-8"))


def test_record_archives_the_report_and_stores_the_numbers_when_below(tmp_path: Path) -> None:
    """Dropping a score, or failing the process because sillward is behind, loses the record."""

    report = _sample_report()
    report_path = tmp_path / "report.json"
    _write_report_file(report_path, report)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"point":"tool.constraint","mode":"shadow"}\n', encoding="utf-8")
    root = tmp_path / "acceptance"
    record = write_acceptance(
        report=report,
        report_path=report_path,
        acceptance_root=root,
        ledgers={"alpha": ledger, "beta": ledger},
    )
    bundle = root / DATE
    stored = _record(bundle)
    assert stored == record
    assert acceptance_exit_code(report) == 0
    assert (bundle / "report.json").read_text(encoding="utf-8") == report_path.read_text(
        encoding="utf-8"
    )
    assert record["date"] == DATE
    assert record["tau_pin"] == "0.4.8"
    assert record["sillward_version"] == "0.1.0"
    assert record["model_id"] == "gpt-5.4"
    assert record["mode_overlay"] == BASE_OVERLAY
    assert record["pass_rate"] == {"sillward": 0.0, "tau": 0.5}
    assert record["blocked_delegations"] == {"sillward": 2, "tau": 0}
    assert record["tasks"] == report["tasks"]
    assert record["below_tau"] is True
    assert record["next_step"] == {
        "promote_at_most": 1,
        "read_ledgers": True,
        "rerun_nightly": True,
    }
    assert record["ledgers"] == {
        "alpha": "ledgers/alpha.jsonl",
        "beta": "ledgers/beta.jsonl",
    }
    archived = (bundle / "ledgers" / "alpha.jsonl").read_text(encoding="utf-8")
    assert archived == ledger.read_text(encoding="utf-8")
    text = json.dumps(record)
    assert TRANSCRIPT_MARK not in text
    assert OPENAI_SECRET not in (bundle / "report.json").read_text(encoding="utf-8")


def test_record_has_no_promotion_step_when_sillward_is_not_below(tmp_path: Path) -> None:
    """A promotion step on a run that is not behind sends the next nightly at the wrong time."""

    report = _sample_report()
    report["pass_rate"] = {"sillward": 0.5, "tau": 0.5}
    report_path = tmp_path / "report.json"
    _write_report_file(report_path, report)
    root = tmp_path / "acceptance"
    record = write_acceptance(
        report=report,
        report_path=report_path,
        acceptance_root=root,
        ledgers={"alpha": report_path},
    )
    assert record["below_tau"] is False
    assert record["pass_rate"] == {"sillward": 0.5, "tau": 0.5}
    assert "next_step" not in record
    assert "ledgers" not in record
    assert not (root / DATE / "ledgers").exists()
    assert acceptance_exit_code(report) == 0


def test_second_archive_keeps_the_first_numbers(tmp_path: Path) -> None:
    """A same-day rerun that overwrites the first record drops the earlier comparison."""

    report = _sample_report()
    report["pass_rate"] = {"sillward": 1.0, "tau": 1.0}
    report_path = tmp_path / "report.json"
    _write_report_file(report_path, report)
    root = tmp_path / "acceptance"
    first = write_acceptance(report=report, report_path=report_path, acceptance_root=root)
    report["pass_rate"] = {"sillward": 0.0, "tau": 1.0}
    report["below_tau"] = True
    _write_report_file(report_path, report)
    second = write_acceptance(report=report, report_path=report_path, acceptance_root=root)
    assert _record(root / DATE)["pass_rate"] == {"sillward": 1.0, "tau": 1.0}
    assert first["pass_rate"] == {"sillward": 1.0, "tau": 1.0}
    assert second["pass_rate"] == {"sillward": 0.0, "tau": 1.0}
    assert (root / f"{DATE}-2" / "record.json").is_file()
    assert _record(root / f"{DATE}-2")["below_tau"] is True


def test_promote_one_shadow_point_prints_the_next_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Printing only the base overlay, or writing user config, skips the human decision."""

    home = tmp_path / "home"
    config = home / ".sillward" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("eval.run.sys.platform", "win32")
    assert main(["--promote", "tool.constraint"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {
        "tool.approval": "active",
        "tool.constraint": "active",
        "turn.continue": "active",
    }
    assert config.read_text(encoding="utf-8") == CONFIG_TEXT


def test_promote_rejects_two_points(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two --promote flags would dial two shadow points in one nightly rerun."""

    monkeypatch.setattr("eval.run.sys.platform", "win32")
    assert main(["--promote", "tool.constraint", "--promote", "tool.injection"]) == 2
    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert captured.err.strip() != ""
    assert "Linux" not in captured.err


def test_promote_rejects_a_point_that_is_already_active(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dialing tool.approval again is not the one shadow point the rerun is allowed to add."""

    monkeypatch.setattr("eval.run.sys.platform", "win32")
    assert main(["--promote", "tool.approval"]) == 2
    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert captured.err.strip() != ""
    assert "Linux" not in captured.err


def test_promote_rejects_an_unknown_point(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown id is not one of the twelve points, so it cannot be the single promotion."""

    monkeypatch.setattr("eval.run.sys.platform", "win32")
    assert main(["--promote", "memory.merge"]) == 2
    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert "Linux" not in captured.err


def test_plan_names_the_acceptance_directory(capsys: pytest.CaptureFixture[str]) -> None:
    """A plan that omits the archive directory hides where the numbers are written."""

    assert acceptance_root() == REPO / "eval" / "acceptance"
    assert nightly_plan()["acceptance"] == "eval/acceptance"
    assert nightly_plan()["overlay"] == BASE_OVERLAY
    assert main(["--plan"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["acceptance"] == "eval/acceptance"
    assert printed["platform"] == "linux"
    assert printed["overlay"] == BASE_OVERLAY


def _comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tasks_dir: Path,
    factory: object,
) -> tuple[dict[str, object], Path]:
    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch)
    root = tmp_path / "acceptance"
    report = run_comparison(
        config_path=REPO / "eval" / "config.toml",
        tasks_dir=tasks_dir,
        work_root=tmp_path / "work",
        report_path=tmp_path / "report.json",
        home=home,
        tau_home=tau_home,
        provider_factory=factory,  # type: ignore[arg-type]
        now=WHEN,
        acceptance_root=root,
    )
    assert (home / ".sillward" / "config.json").read_text(encoding="utf-8") == CONFIG_TEXT
    return report, root


def test_comparison_archives_every_discovered_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archiving one task while the directory has two drops part of the comparison."""

    tasks_dir = tmp_path / "tasks"
    source = REPO / "eval" / "tasks" / "sum_function"
    shutil.copytree(source, tasks_dir / "alpha")
    shutil.copytree(source, tasks_dir / "beta")
    providers: list[SolvingProvider] = []

    def factory(arm: str) -> SolvingProvider:
        del arm
        provider = SolvingProvider()
        providers.append(provider)
        return provider

    report, root = _comparison(tmp_path, monkeypatch, tasks_dir=tasks_dir, factory=factory)
    record = _record(root / DATE)
    assert [row["id"] for row in record["tasks"]] == ["alpha", "beta"]
    assert record["model_id"] == "gpt-5.4"
    assert record["tau_pin"] == "0.4.8"
    assert record["sillward_version"] == "0.1.0"
    assert record["mode_overlay"] == BASE_OVERLAY
    assert record["pass_rate"] == {"sillward": 1.0, "tau": 1.0}
    assert record["below_tau"] is False
    assert "next_step" not in record
    assert report["tasks"][0]["tau"]["turns"] == 2
    assert record["tasks"][0]["tau"]["turns"] == 2
    assert record["tasks"][1]["sillward"]["passed"] is True
    text = (root / DATE / "record.json").read_text(encoding="utf-8")
    archive = (root / DATE / "report.json").read_text(encoding="utf-8")
    assert OPENAI_SECRET not in text
    assert JEV_SECRET not in text
    assert TRANSCRIPT_MARK not in text
    assert TRANSCRIPT_MARK not in archive
    assert INSTRUCTION_MARK not in archive
    assert all(model == "gpt-5.4" for provider in providers for model in provider.models)


def test_lower_sillward_rate_archives_the_ledger_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising on a lower pass rate, or dropping the ledger, blocks the next step."""

    def factory(arm: str) -> SolvingProvider | IdleProvider:
        if arm == "tau":
            return SolvingProvider()
        return IdleProvider()

    report, root = _comparison(
        tmp_path,
        monkeypatch,
        tasks_dir=REPO / "eval" / "tasks",
        factory=factory,
    )
    assert acceptance_exit_code(report) == 0
    record = _record(root / DATE)
    assert record["below_tau"] is True
    assert record["pass_rate"] == {"sillward": 0.0, "tau": 1.0}
    assert record["mode_overlay"] == BASE_OVERLAY
    assert record["next_step"]["promote_at_most"] == 1
    assert record["next_step"]["read_ledgers"] is True
    assert record["next_step"]["rerun_nightly"] is True
    workspace_ledger = (
        tmp_path / "work" / "sillward" / "sum_function" / ".sillward" / "ledger.jsonl"
    )
    archived = root / DATE / "ledgers" / "sum_function.jsonl"
    assert archived.read_text(encoding="utf-8") == workspace_ledger.read_text(encoding="utf-8")
    assert '"point"' in archived.read_text(encoding="utf-8")
    assert record["ledgers"] == {"sum_function": "ledgers/sum_function.jsonl"}


def test_one_promoted_point_is_active_on_the_sillward_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded promotion the sillward arm never applies leaves that point shadow."""

    def factory(arm: str) -> SolvingProvider:
        del arm
        return SolvingProvider()

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch)
    monkeypatch.setenv(
        "SILLWARD_MODE_OVERLAY",
        json.dumps({**BASE_OVERLAY, "tool.constraint": "active"}, separators=(",", ":")),
    )
    root = tmp_path / "acceptance"
    run_comparison(
        config_path=REPO / "eval" / "config.toml",
        tasks_dir=REPO / "eval" / "tasks",
        work_root=tmp_path / "work",
        report_path=tmp_path / "report.json",
        home=home,
        tau_home=tau_home,
        provider_factory=factory,
        now=WHEN,
        acceptance_root=root,
    )
    assert (home / ".sillward" / "config.json").read_text(encoding="utf-8") == CONFIG_TEXT
    record = _record(root / DATE)
    assert record["mode_overlay"] == {
        "tool.approval": "active",
        "tool.constraint": "active",
        "turn.continue": "active",
    }
    ledger = (
        tmp_path / "work" / "sillward" / "sum_function" / ".sillward" / "ledger.jsonl"
    ).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines() if line]
    constraint = [row for row in rows if row["point"] == "tool.constraint"]
    assert constraint[0]["mode"] == "active"


def test_two_promotions_do_not_start_either_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two extra active points would change the nightly overlay by more than one shadow point."""

    calls: list[str] = []

    def factory(arm: str) -> IdleProvider:
        calls.append(arm)
        return IdleProvider()

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    monkeypatch.setenv(
        "SILLWARD_MODE_OVERLAY",
        json.dumps(
            {**BASE_OVERLAY, "tool.constraint": "active", "tool.injection": "active"},
            separators=(",", ":"),
        ),
    )
    with pytest.raises(AcceptanceError):
        run_comparison(
            config_path=REPO / "eval" / "config.toml",
            tasks_dir=REPO / "eval" / "tasks",
            work_root=tmp_path / "work",
            report_path=tmp_path / "report.json",
            home=home,
            tau_home=tau_home,
            provider_factory=factory,
            now=WHEN,
            acceptance_root=tmp_path / "acceptance",
        )
    assert calls == []
    assert not (tmp_path / "report.json").exists()


def test_linux_entry_archives_every_task_for_the_pinned_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping the archive, or asking a different model, is not the Linux comparison."""

    home = tmp_path / "home"
    tau_home = tmp_path / "tau-home"
    _isolate(monkeypatch, home, tau_home)
    _install_judge(monkeypatch)
    provider = SolvingProvider()
    monkeypatch.setattr("eval.run.sys.platform", "linux")
    monkeypatch.setattr("eval.run._pinned_provider", lambda: provider)
    monkeypatch.setattr("eval.run.acceptance_root", lambda: tmp_path / "acceptance")
    monkeypatch.setattr(
        "eval.run.report_file", lambda stamp: tmp_path / "reports" / f"{stamp}.json"
    )
    monkeypatch.setattr("eval.run._utc_date", lambda now: DATE)
    monkeypatch.setattr("eval.run.Path.home", lambda: home)
    monkeypatch.setattr("eval.run._tau_home", lambda: tau_home)
    assert main([]) == 0
    expected = [task.id for task in discover_tasks(REPO / "eval" / "tasks")]
    record = _record(tmp_path / "acceptance" / DATE)
    assert [row["id"] for row in record["tasks"]] == expected
    assert record["model_id"] == "gpt-5.4"
    assert record["tau_pin"] == "0.4.8"
    assert provider.models
    assert set(provider.models) == {"gpt-5.4"}
    assert (tmp_path / "acceptance" / DATE / "report.json").is_file()
    assert acceptance_exit_code(record) == 0


def test_windows_entry_does_not_write_the_acceptance_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows process that starts the pinned model is not the Linux comparison."""

    monkeypatch.setattr("eval.run.sys.platform", "win32")
    monkeypatch.setattr("eval.run.acceptance_root", lambda: tmp_path / "acceptance")
    created: list[str] = []
    monkeypatch.setattr("eval.run._pinned_provider", lambda: created.append("provider"))
    assert main([]) == 2
    assert created == []
    assert not (tmp_path / "acceptance").exists()


def test_linux_entry_refuses_two_promotions_before_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Creating the pinned provider for two promotions starts a forbidden run."""

    monkeypatch.setattr("eval.run.sys.platform", "linux")
    monkeypatch.setenv(
        "SILLWARD_MODE_OVERLAY",
        json.dumps(
            {**BASE_OVERLAY, "tool.constraint": "active", "swarm.patch": "active"},
            separators=(",", ":"),
        ),
    )
    created: list[str] = []
    monkeypatch.setattr("eval.run._pinned_provider", lambda: created.append("provider"))
    monkeypatch.setattr("eval.run.acceptance_root", lambda: tmp_path / "acceptance")
    assert main([]) == 2
    assert created == []
    assert not (tmp_path / "acceptance").exists()
    assert capsys.readouterr().err.strip() != ""
