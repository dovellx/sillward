"""Load ``~/.sillward/config.json`` and apply the process mode overlay.

The project directory is not a configuration source. ``SILLWARD_MODE_OVERLAY``
changes only the ``Config`` returned for the mapping that carried it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sillward.kernel.points import POINTS

_MODES = frozenset({"active", "shadow", "off"})
_JUDGES = frozenset({"llm", "jev"})
_THRESHOLD_KEYS = ("approval", "approval_check", "routing_override")
_DEFAULT_THRESHOLDS = {"approval": 0.8, "approval_check": 0.7, "routing_override": 0.8}


class ConfigError(ValueError):
    """The user config or the process overlay cannot be used."""


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Probabilities the later gates compare against a verdict."""

    approval: float
    approval_check: float
    routing_override: float


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved settings for one process."""

    judge: str
    jev_model: str | None
    modes: Mapping[str, str]
    thresholds: Thresholds
    record_state: bool

    def mode(self, point: str) -> str:
        """Return the mode for a declared point."""
        try:
            return self.modes[point]
        except KeyError:
            raise ConfigError(f"unknown point {point}") from None


def load_config(*, home: Path, project: Path, environ: Mapping[str, str]) -> Config:
    """Load the user file under ``home`` and overlay modes from ``environ``.

    ``project`` is accepted so the caller names the workspace. No file inside
    it is opened. A missing user file yields the plan's defaults: judge
    ``llm``, every point ``shadow``, and the standard thresholds.
    """
    _ = project
    raw = _read_user_config(home / ".sillward" / "config.json")
    modes = _apply_overlay(_resolve_modes(raw.get("modes")), environ)
    return Config(
        judge=_judge_name(raw.get("judge")),
        jev_model=_optional_text(raw.get("jev_model"), field_name="jev_model"),
        modes=modes,
        thresholds=_thresholds(raw.get("thresholds")),
        record_state=_flag(raw.get("record_state"), field_name="record_state"),
    )


def _read_user_config(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError("user config is not a JSON object") from exc
    if not isinstance(loaded, dict):
        raise ConfigError("user config is not a JSON object")
    return loaded


def _resolve_modes(raw: object) -> dict[str, str]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("modes must be an object")
    default: str | None = None
    overrides: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ConfigError("mode names must be strings")
        if key == "default":
            default = _mode_value(value)
            continue
        if key not in POINTS:
            raise ConfigError(f"unknown point {key}")
        overrides[key] = _mode_value(value)
    resolved: dict[str, str] = {}
    for point_id, spec in POINTS.items():
        if point_id in overrides:
            resolved[point_id] = overrides[point_id]
        elif default is not None:
            resolved[point_id] = default
        else:
            resolved[point_id] = spec.default_mode
    return resolved


def _apply_overlay(modes: dict[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    raw = environ.get("SILLWARD_MODE_OVERLAY")
    if raw is None or not raw.strip():
        return modes
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError("SILLWARD_MODE_OVERLAY is not a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("SILLWARD_MODE_OVERLAY is not a JSON object")
    updated = dict(modes)
    for key, value in parsed.items():
        if not isinstance(key, str) or key not in POINTS:
            raise ConfigError(f"unknown point in SILLWARD_MODE_OVERLAY: {key}")
        updated[key] = _mode_value(value)
    return updated


def _mode_value(value: object) -> str:
    if not isinstance(value, str) or value not in _MODES:
        raise ConfigError("mode must be active, shadow, or off")
    return value


def _judge_name(value: object) -> str:
    if value is None:
        return "llm"
    if not isinstance(value, str) or value not in _JUDGES:
        raise ConfigError("judge must be llm or jev")
    return value


def _optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string")
    return value.strip()


def _flag(value: object, *, field_name: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a boolean")
    return value


def _thresholds(raw: object) -> Thresholds:
    values = dict(_DEFAULT_THRESHOLDS)
    if raw is None:
        return Thresholds(
            approval=values["approval"],
            approval_check=values["approval_check"],
            routing_override=values["routing_override"],
        )
    if not isinstance(raw, dict):
        raise ConfigError("thresholds must be an object")
    for key, value in raw.items():
        if key not in _THRESHOLD_KEYS:
            raise ConfigError(f"unknown threshold {key}")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"threshold {key} must be a number")
        values[key] = float(value)
    return Thresholds(
        approval=values["approval"],
        approval_check=values["approval_check"],
        routing_override=values["routing_override"],
    )
