"""Run one Python task on stock tau and on sillward, then write a report.

The nightly entry is Linux. ``--plan`` prints the platform and the mode overlay
without starting a model. A non-Linux process stops before either arm runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tau_agent.messages import AssistantMessage, TextContent, ToolResultMessage
from tau_agent.provider_events import AssistantDoneEvent
from tau_agent.session import InMemorySessionStorage
from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths
from tau_coding.session import CodingSession, CodingSessionConfig

import sillward.extension as extension_module
from eval.report import ArmScore, pinned_tau_version, render_report, sillward_version, write_report

ROOT = Path(__file__).resolve().parents[1]
COMPARISON_OVERLAY = {"tool.approval": "active", "turn.continue": "active"}
_OVERLAY_ENV = "SILLWARD_MODE_OVERLAY"
_ARMS = ("tau", "sillward")


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """The pinned provider, model, turn budget, and time limit."""

    provider: str
    model: str
    turns: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class Task:
    """One task directory: instruction, starter tree, hidden tests, score command."""

    id: str
    instruction: str
    start: Path
    hidden: Path
    score_command: str


class _Env:
    """Apply environment updates and put the previous values back."""

    def __init__(self, updates: Mapping[str, str | None]) -> None:
        self._updates = dict(updates)
        self._previous: dict[str, str | None] = {}

    def __enter__(self) -> _Env:
        for key, value in self._updates.items():
            self._previous[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc: object) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _CappedProvider:
    """Forward model calls until the turn budget is spent, then stop.

    A call that is cancelled before it finishes does not count. The stop
    message that enforces the budget is not forwarded and does not count.
    """

    def __init__(self, inner: object, max_turns: int) -> None:
        self._inner = inner
        self._max_turns = max_turns
        self.turns = 0

    async def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[object],
        tools: list[object],
        signal: object = None,
        session_id: str | None = None,
    ) -> AsyncIterator[object]:
        if self.turns >= self._max_turns:
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text="Stopped after the turn limit.")],
                    stop_reason="stop",
                ),
            )
            return
        stream = self._inner.stream_response(  # type: ignore[attr-defined]
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            signal=signal,
            session_id=session_id,
        )
        finished = False
        try:
            async for event in stream:
                yield event
            finished = True
        finally:
            if finished:
                self.turns += 1


def load_config(path: Path) -> EvalConfig:
    """Read the pinned model, turn budget, and time limit."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return EvalConfig(
        provider=str(data["provider"]),
        model=str(data["model"]),
        turns=int(data["turns"]),
        timeout_seconds=int(data["timeout_seconds"]),
    )


def discover_tasks(tasks_dir: Path) -> list[Task]:
    """Load every task directory, ordered by id."""
    tasks: list[Task] = []
    if not tasks_dir.is_dir():
        return tasks
    for path in sorted(item for item in tasks_dir.iterdir() if item.is_dir()):
        instruction_path = path / "instruction.md"
        score_path = path / "score.txt"
        start = path / "start"
        hidden = path / "hidden"
        if not instruction_path.is_file() or not score_path.is_file():
            continue
        if not start.is_dir() or not hidden.is_dir():
            continue
        tasks.append(
            Task(
                id=path.name,
                instruction=instruction_path.read_text(encoding="utf-8"),
                start=start,
                hidden=hidden,
                score_command=score_path.read_text(encoding="utf-8").strip(),
            )
        )
    return tasks


def score_task(task: Task, workspace: Path) -> bool:
    """Copy the workspace, add the hidden tests, and run the score command.

    The hidden tests are not written back into ``workspace``. Passing means
    the score command exited 0. Pytest is the command the task declares.
    """
    with tempfile.TemporaryDirectory() as raw:
        staging = Path(raw) / "work"
        shutil.copytree(workspace, staging)
        shutil.copytree(task.hidden, staging / "hidden")
        env = os.environ.copy()
        env.pop("PYTEST_ADDOPTS", None)
        env["PYTHONPATH"] = str(staging)
        completed = subprocess.run(
            _score_argv(task.score_command),
            cwd=staging,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0


def nightly_plan() -> dict[str, object]:
    """Describe the nightly entry: Linux, and the overlay both docs and CI set."""
    return {
        "command": "uv run python -m eval.run",
        "overlay": dict(COMPARISON_OVERLAY),
        "platform": "linux",
    }


def run_comparison(
    *,
    config_path: Path,
    tasks_dir: Path,
    work_root: Path,
    report_path: Path,
    home: Path,
    tau_home: Path,
    provider_factory: Callable[[str], object],
    now: datetime | None = None,
) -> dict[str, object]:
    """Run every task on both arms and write one report."""
    config = load_config(config_path)
    tasks = discover_tasks(tasks_dir)
    if not tasks:
        raise RuntimeError(f"no tasks in {tasks_dir}")
    recorded_on = _utc_date(now)
    homes = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TAU_HOME": str(tau_home),
    }
    with _Env(homes):
        scores = asyncio.run(
            _run_tasks(
                config=config,
                tasks=tasks,
                work_root=work_root,
                tau_home=tau_home,
                provider_factory=provider_factory,
            )
        )
    report = render_report(
        date=recorded_on,
        tau_pin=pinned_tau_version(ROOT / "pyproject.toml"),
        sillward_version=sillward_version(),
        model_id=config.model,
        mode_overlay=COMPARISON_OVERLAY,
        scores=scores,
    )
    write_report(report, report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    """Print the nightly plan, or refuse to start the comparison off Linux."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--plan" in args:
        print(json.dumps(nightly_plan(), sort_keys=True))
        return 0
    if sys.platform != "linux":
        print(
            "The nightly comparison runs on Linux. "
            "Use a GitHub-hosted Ubuntu runner or a local Linux container.",
            file=sys.stderr,
        )
        return 2
    _run_nightly()
    return 0


def _run_nightly() -> None:
    """Start both arms with the pinned provider. This is the Linux entry."""
    from tau_coding.provider_config import load_provider_settings, resolve_provider_selection
    from tau_coding.provider_runtime import create_model_provider

    config = load_config(ROOT / "eval" / "config.toml")
    settings = load_provider_settings()
    selection = resolve_provider_selection(
        settings,
        provider_name=config.provider,
        model=config.model,
    )
    provider = create_model_provider(selection.provider, model=selection.model)
    stamp = _utc_date(None)
    report_path = ROOT / "eval" / "reports" / f"{stamp}.json"
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_comparison(
                config_path=ROOT / "eval" / "config.toml",
                tasks_dir=ROOT / "eval" / "tasks",
                work_root=root / "work",
                report_path=report_path,
                home=Path.home(),
                tau_home=TauPaths().home,
                provider_factory=lambda _arm: provider,
            )
    finally:
        asyncio.run(provider.aclose())


def _utc_date(now: datetime | None) -> str:
    moment = datetime.now(UTC) if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date().isoformat()


def _score_argv(command: str) -> list[str]:
    parts = shlex.split(command, posix=True)
    if parts and Path(parts[0]).name in {"python", "python3", "python.exe"}:
        parts[0] = sys.executable
    return parts


def _extension_file() -> Path:
    file = extension_module.__file__
    if file is None:
        raise RuntimeError("sillward extension has no source file")
    return Path(file).resolve()


async def _run_tasks(
    *,
    config: EvalConfig,
    tasks: list[Task],
    work_root: Path,
    tau_home: Path,
    provider_factory: Callable[[str], object],
) -> list[ArmScore]:
    scores: list[ArmScore] = []
    for task in tasks:
        for arm in _ARMS:
            scores.append(
                await _run_arm(
                    config=config,
                    task=task,
                    arm=arm,
                    workspace=work_root / arm / task.id,
                    tau_home=tau_home,
                    provider=provider_factory(arm),
                )
            )
    return scores


async def _run_arm(
    *,
    config: EvalConfig,
    task: Task,
    arm: str,
    workspace: Path,
    tau_home: Path,
    provider: object,
) -> ArmScore:
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(task.start, workspace)
    capped = _CappedProvider(provider, config.turns)
    overlay: dict[str, str | None]
    if arm == "sillward":
        overlay = {_OVERLAY_ENV: json.dumps(COMPARISON_OVERLAY, separators=(",", ":"))}
    else:
        overlay = {_OVERLAY_ENV: None}
    with _Env(overlay):
        session = await _open_session(
            workspace=workspace,
            tau_home=tau_home,
            provider=capped,
            model=config.model,
            provider_name=config.provider,
            load_extension=arm == "sillward",
        )
        try:
            failures = [
                item.message
                for item in session.resource_diagnostics
                if "setup failed" in item.message
            ]
            if failures:
                raise RuntimeError("; ".join(failures))
            try:
                async with asyncio.timeout(config.timeout_seconds):
                    async for _event in session.prompt(task.instruction):
                        pass
            except TimeoutError:
                pass
            blocked = _blocked_delegations(session)
        finally:
            await session.aclose()
    return ArmScore(
        task_id=task.id,
        arm=arm,
        passed=score_task(task, workspace),
        turns=capped.turns,
        blocked_delegations=blocked,
    )


def _blocked_delegations(session: CodingSession) -> int:
    count = 0
    for message in session.messages:
        if not isinstance(message, ToolResultMessage) or message.tool_name != "agent":
            continue
        if "Tool call blocked" in message.text:
            count += 1
    return count


async def _open_session(
    *,
    workspace: Path,
    tau_home: Path,
    provider: object,
    model: str,
    provider_name: str,
    load_extension: bool,
) -> CodingSession:
    agents = tau_home.parent / "agents"
    tau_home.mkdir(parents=True, exist_ok=True)
    agents.mkdir(parents=True, exist_ok=True)
    paths = TauPaths(home=tau_home, agents_home=agents)
    extension_paths = (_extension_file(),) if load_extension else ()
    return await CodingSession.load(
        CodingSessionConfig(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            storage=InMemorySessionStorage(),
            cwd=workspace,
            system="Follow the user message. Edit the project files to complete the task.",
            resource_paths=TauResourcePaths(
                root=tau_home,
                cwd=workspace,
                agents_root=agents,
                paths=paths,
                project_resources_enabled=False,
            ),
            provider_name=provider_name,
            skills_enabled=False,
            extensions_enabled=False,
            project_extensions_enabled=False,
            extension_paths=extension_paths,
            trust_override="approve",
            auto_compact_enabled=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
