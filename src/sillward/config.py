"""Load the project ``.sillward/config.json`` and apply the process mode overlay.

The home directory is not a configuration source. ``SILLWARD_MODE_OVERLAY``
changes only the ``Config`` returned for the mapping that carried it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sillward.kernel.points import POINTS

_MODES = frozenset({"active", "shadow", "off"})
_JUDGES = frozenset({"llm", "jev"})
_MODEL_TARGETS = frozenset({"agent", "judge"})
_MODEL_FIELDS = frozenset({"provider", "model", "api_key"})
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
class ModelTarget:
    """A provider name, a model id, and the key for that role."""

    provider: str | None
    model: str | None
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class Models:
    """Coding-session and llm-judge model selections from the project file."""

    agent: ModelTarget | None
    judge: ModelTarget | None


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved settings for one process."""

    judge: str
    jev_model: str | None
    llm_api_key: str | None = field(repr=False)
    modes: Mapping[str, str]
    thresholds: Thresholds
    record_state: bool
    models: Models

    def mode(self, point: str) -> str:
        """Return the mode for a declared point."""
        try:
            return self.modes[point]
        except KeyError:
            raise ConfigError(f"unknown point {point}") from None


def load_config(*, project: Path, environ: Mapping[str, str]) -> Config:
    """Load ``project / ".sillward" / "config.json"`` and overlay modes from ``environ``.

    The home directory is not opened. Other files in ``project``, such as
    ``config.json`` at the project root, are not opened either. A missing
    project file yields the plan's defaults: judge ``llm``, every point
    ``shadow``, the standard thresholds, and no model targets.
    """
    raw = _read_user_config(project / ".sillward" / "config.json")
    modes = _apply_overlay(_resolve_modes(raw.get("modes")), environ)
    return Config(
        judge=_judge_name(raw.get("judge")),
        jev_model=_optional_text(raw.get("jev_model"), field_name="jev_model"),
        llm_api_key=_optional_text(raw.get("llm_api_key"), field_name="llm_api_key"),
        modes=modes,
        thresholds=_thresholds(raw.get("thresholds")),
        record_state=_flag(raw.get("record_state"), field_name="record_state"),
        models=_models(raw.get("models")),
    )


def _read_user_config(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError("project config is not a JSON object") from exc
    if not isinstance(loaded, dict):
        raise ConfigError("project config is not a JSON object")
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


def _models(raw: object) -> Models:
    if raw is None:
        return Models(agent=None, judge=None)
    if not isinstance(raw, dict):
        raise ConfigError("models must be an object")
    for key in raw:
        if key not in _MODEL_TARGETS:
            raise ConfigError(f"unknown model target {key}")
    return Models(
        agent=_model_target("agent", raw.get("agent")),
        judge=_model_target("judge", raw.get("judge")),
    )


def _model_target(name: str, raw: object) -> ModelTarget | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"models.{name} must be an object")
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    for key, value in raw.items():
        if key not in _MODEL_FIELDS:
            raise ConfigError(f"unknown field models.{name}.{key}")
        parsed = _model_field(value, name=name, field=key)
        if key == "provider":
            provider = parsed
        elif key == "model":
            model = parsed
        else:
            api_key = parsed
    if provider is None and model is None and api_key is None:
        return None
    return ModelTarget(provider=provider, model=model, api_key=api_key)


def _model_field(value: object, *, name: str, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"models.{name}.{field} must be a non-empty string")
    return value.strip()


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
