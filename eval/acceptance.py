"""Archive a comparison report and record its numbers.

A pass rate below stock tau is part of the record. It does not fail the
process. The next nightly may promote one shadow point, and only one.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from sillward.kernel.points import POINTS

# The first comparison turns these two points on. A later nightly may add one
# more shadow point. It does not turn either of these off.
NIGHTLY_BASE: dict[str, str] = {"tool.approval": "active", "turn.continue": "active"}

_NEXT_STEP = {"promote_at_most": 1, "read_ledgers": True, "rerun_nightly": True}


class AcceptanceError(Exception):
    """The next nightly overlay is not a single shadow-point promotion."""


def acceptance_exit_code(report: Mapping[str, object]) -> int:
    """Return 0 whether or not sillward's pass rate is below tau.

    The numbers have to be present. They are not a merge condition.
    """
    rates = _rates(report)
    _ = rates["sillward"]
    _ = rates["tau"]
    return 0


def overlay_promoting(points: Sequence[str]) -> dict[str, str]:
    """Return the nightly overlay with exactly one shadow point set to active."""
    if len(points) != 1:
        raise AcceptanceError("promote exactly one shadow point")
    point = points[0]
    if point not in POINTS:
        raise AcceptanceError(f"unknown point {point}")
    if point in NIGHTLY_BASE:
        raise AcceptanceError(f"{point} is already active")
    overlay = dict(NIGHTLY_BASE)
    overlay[point] = "active"
    return overlay


def effective_overlay(environ: Mapping[str, str]) -> dict[str, str]:
    """Return the base overlay plus at most one promoted shadow point.

    An empty overlay, and an overlay that only repeats the base, stay on the
    base. Two promotions, an unknown point, or a change to a base point raise.
    """
    raw = environ.get("SILLWARD_MODE_OVERLAY")
    overlay = dict(NIGHTLY_BASE)
    if raw is None or not raw.strip():
        return overlay
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AcceptanceError("SILLWARD_MODE_OVERLAY is not a JSON object") from exc
    if not isinstance(parsed, dict):
        raise AcceptanceError("SILLWARD_MODE_OVERLAY is not a JSON object")
    extras: list[str] = []
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise AcceptanceError("SILLWARD_MODE_OVERLAY is not a JSON object")
        if key in NIGHTLY_BASE:
            if value != NIGHTLY_BASE[key]:
                raise AcceptanceError("the nightly base points stay active")
            continue
        if key not in POINTS:
            raise AcceptanceError(f"unknown point {key}")
        if value != "active":
            raise AcceptanceError("a promoted point has to be active")
        extras.append(key)
    if len(extras) > 1:
        raise AcceptanceError("promote exactly one shadow point")
    for key in extras:
        overlay[key] = "active"
    return overlay


def write_acceptance(
    *,
    report: Mapping[str, object],
    report_path: Path,
    acceptance_root: Path,
    ledgers: Mapping[str, Path] | None = None,
) -> dict[str, object]:
    """Copy the report into the archive and write a record of its numbers.

    A later call on the same UTC date uses ``<date>-2`` and leaves the first
    record in place. Ledger files are copied only when sillward is below tau.
    """
    rates = _rates(report)
    below = float(rates["sillward"]) < float(rates["tau"])
    date = report["date"]
    if not isinstance(date, str) or not date:
        raise AcceptanceError("date is missing")
    bundle = _bundle_dir(acceptance_root, date)
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report_path, bundle / "report.json")
    record: dict[str, object] = {
        "below_tau": below,
        "blocked_delegations": _pair(report["blocked_delegations"]),
        "date": date,
        "mode_overlay": _overlay(report["mode_overlay"]),
        "model_id": _text(report["model_id"]),
        "pass_rate": {"sillward": rates["sillward"], "tau": rates["tau"]},
        "sillward_version": _text(report["sillward_version"]),
        "tasks": _tasks(report["tasks"]),
        "tau_pin": _text(report["tau_pin"]),
    }
    if below:
        record["ledgers"] = _copy_ledgers(bundle, ledgers or {})
        record["next_step"] = dict(_NEXT_STEP)
    text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    (bundle / "record.json").write_text(text, encoding="utf-8")
    return json.loads(text)


def _rates(report: Mapping[str, object]) -> Mapping[str, int | float]:
    rates = report["pass_rate"]
    if not isinstance(rates, Mapping):
        raise AcceptanceError("pass rate is missing")
    checked: dict[str, int | float] = {}
    for arm in ("sillward", "tau"):
        value = rates[arm]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise AcceptanceError("pass rate is missing")
        checked[arm] = value
    return checked


def _pair(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AcceptanceError("blocked delegations are missing")
    return {"sillward": value["sillward"], "tau": value["tau"]}


def _overlay(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AcceptanceError("mode overlay is missing")
    overlay: dict[str, str] = {}
    for key, mode in value.items():
        if not isinstance(key, str) or not isinstance(mode, str):
            raise AcceptanceError("mode overlay is missing")
        overlay[key] = mode
    return overlay


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise AcceptanceError("report field is missing")
    return value


def _tasks(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AcceptanceError("tasks are missing")
    copied: list[dict[str, object]] = []
    for task in value:
        if not isinstance(task, Mapping):
            raise AcceptanceError("tasks are missing")
        copied.append(
            {
                "id": _text(task["id"]),
                "sillward": _arm(task["sillward"]),
                "tau": _arm(task["tau"]),
            }
        )
    return copied


def _arm(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AcceptanceError("tasks are missing")
    return {
        "blocked_delegations": value["blocked_delegations"],
        "passed": value["passed"],
        "turns": value["turns"],
    }


def _copy_ledgers(bundle: Path, ledgers: Mapping[str, Path]) -> dict[str, str]:
    relative: dict[str, str] = {}
    if not ledgers:
        return relative
    destination = bundle / "ledgers"
    destination.mkdir(parents=True, exist_ok=True)
    for task_id in sorted(ledgers):
        target = destination / f"{task_id}.jsonl"
        shutil.copyfile(ledgers[task_id], target)
        relative[task_id] = f"ledgers/{task_id}.jsonl"
    return relative


def _bundle_dir(acceptance_root: Path, date: str) -> Path:
    direct = acceptance_root / date
    if not direct.exists():
        return direct
    index = 2
    while (acceptance_root / f"{date}-{index}").exists():
        index += 1
    return acceptance_root / f"{date}-{index}"
