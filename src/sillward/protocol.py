"""Judge requests and verdicts. This module does not know about tau."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Question(StrEnum):
    """Whether the judge picks a label or a score."""

    CHOICE = "choice"
    SCORE = "score"


class ProtocolFailure(Exception):
    """The verdict omitted a probability or answered outside the candidates."""


@dataclass(frozen=True, slots=True)
class Request:
    """One question for a decision point.

    ``frame_summary`` and ``state`` may hold task text, tool parameters, and
    file contents. Callers that write a ledger must not copy those through.
    """

    point: str
    question: Question
    candidates: tuple[str, ...]
    frame_summary: str = ""
    state: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Verdict:
    """A judge's answer. ``elapsed_ms`` is the adapter's own duration."""

    answer: str
    probabilities: Mapping[str, float]
    elapsed_ms: float
    adapter: str


def check_verdict(request: Request, verdict: Verdict) -> None:
    """Reject a verdict the protocol does not allow.

    Every candidate needs a numeric probability. The answer has to be one of
    the candidates. Anything else is a judge failure.
    """
    if verdict.answer not in request.candidates:
        raise ProtocolFailure("answer is outside the candidates")
    probabilities = verdict.probabilities
    if not isinstance(probabilities, Mapping):
        raise ProtocolFailure("probabilities are missing")
    for candidate in request.candidates:
        probability = probabilities.get(candidate)
        if isinstance(probability, bool) or not isinstance(probability, int | float):
            raise ProtocolFailure(f"probability is missing for {candidate}")
