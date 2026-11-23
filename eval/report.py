"""Build the comparison report. It records scores, never the transcript."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArmScore:
    """One arm's score for one task. No transcript and no secrets."""

    task_id: str
    arm: str
    passed: bool
    turns: int
    blocked_delegations: int


def pinned_tau_version(pyproject: Path) -> str:
    """Return the ``tau-ai==`` pin written in ``pyproject.toml``."""
    text = pyproject.read_text(encoding="utf-8")
    marker = "tau-ai=="
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("tau-ai is not pinned in pyproject.toml")
    pin: list[str] = []
    for char in text[start + len(marker) :]:
        if char.isalnum() or char in ".-":
            pin.append(char)
            continue
        break
    if not pin:
        raise RuntimeError("tau-ai pin is empty")
    return "".join(pin)


def sillward_version() -> str:
    """Return the installed sillward version."""
    return version("sillward")


def render_report(
    *,
    date: str,
    tau_pin: str,
    sillward_version: str,
    model_id: str,
    mode_overlay: Mapping[str, str],
    scores: Sequence[ArmScore],
) -> dict[str, object]:
    """Assemble the report fields from arm scores."""
    order: list[str] = []
    grouped: dict[str, dict[str, ArmScore]] = {}
    for score in scores:
        arms = grouped.get(score.task_id)
        if arms is None:
            order.append(score.task_id)
            arms = {}
            grouped[score.task_id] = arms
        arms[score.arm] = score
    tasks: list[dict[str, object]] = []
    for task_id in order:
        arms = grouped[task_id]
        tasks.append(
            {
                "id": task_id,
                "sillward": _arm_fields(arms["sillward"]),
                "tau": _arm_fields(arms["tau"]),
            }
        )
    total = len(tasks)

    def rate(arm: str) -> float:
        if total == 0:
            return 0.0
        passed = sum(1 for task_id in order if grouped[task_id][arm].passed)
        return passed / total

    def blocked(arm: str) -> int:
        return sum(grouped[task_id][arm].blocked_delegations for task_id in order)

    return {
        "blocked_delegations": {"sillward": blocked("sillward"), "tau": blocked("tau")},
        "date": date,
        "mode_overlay": dict(mode_overlay),
        "model_id": model_id,
        "pass_rate": {"sillward": rate("sillward"), "tau": rate("tau")},
        "sillward_version": sillward_version,
        "tasks": tasks,
        "tau_pin": tau_pin,
    }


def _arm_fields(score: ArmScore) -> dict[str, object]:
    return {
        "blocked_delegations": score.blocked_delegations,
        "passed": score.passed,
        "turns": score.turns,
    }


def write_report(report: Mapping[str, object], path: Path) -> None:
    """Write the report JSON. The mapping is stored as given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
