"""TypeSafe System One adapter.

Jev is not a chat model. A call posts ``{model, state, questions}`` to
TypeSafe's System One endpoint, the same request mu sends for ``jev-direct``.
The default address is ``https://api.typesafe.ai/v1/systemone`` and the
default model is ``jev-latest``. ``TYPESAFE_API_KEY`` is the bearer token.
"""

from __future__ import annotations

from collections.abc import Mapping

from sillward.judges.base import JudgeFailure
from sillward.judges.remote import PostingClient, _post_json
from sillward.protocol import ProtocolFailure, Question, Request, Verdict, check_verdict

TYPESAFE_SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
_QUESTION_ID = "verdict"
_CHOICE_INSTRUCTIONS = "Choose one candidate for this decision point."
_SCORE_INSTRUCTIONS = "Place this decision point on the rubric."


class JevJudge:
    """Call TypeSafe System One."""

    name = "jev"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None,
        client: PostingClient,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = client

    def judge(self, request: Request) -> Verdict:
        """Ask System One and read the choice or the score level."""
        if not self._api_key:
            raise JudgeFailure("missing api key")
        supplied = (self._base_url or "").strip()
        url = (supplied or TYPESAFE_SYSTEM_ONE_URL).rstrip("/")
        model = self._model.strip() or DEFAULT_MODEL
        try:
            body, elapsed_ms = _post_json(
                self._client,
                url=url,
                api_key=self._api_key,
                payload=_payload(model, request),
            )
        except TypeError as exc:
            raise JudgeFailure("protocol") from exc
        verdict = _verdict(body, request, elapsed_ms=elapsed_ms)
        try:
            check_verdict(request, verdict)
        except ProtocolFailure as exc:
            raise JudgeFailure("protocol") from exc
        return verdict


def _payload(model: str, request: Request) -> dict[str, object]:
    return {
        "model": model,
        "state": {
            "point": request.point,
            "frame_summary": request.frame_summary,
            "state": dict(request.state),
        },
        "questions": {_QUESTION_ID: _question(request)},
    }


def _question(request: Request) -> dict[str, object]:
    if request.question is Question.SCORE:
        return {
            "type": "score",
            "instructions": _SCORE_INSTRUCTIONS,
            "criteria": list(request.candidates),
        }
    return {
        "type": "choice",
        "instructions": _CHOICE_INSTRUCTIONS,
        "criteria": {candidate: candidate for candidate in request.candidates},
    }


def _verdict(body: dict[str, object], request: Request, *, elapsed_ms: float) -> Verdict:
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise JudgeFailure("protocol")
    raw = answers.get(_QUESTION_ID)
    if not isinstance(raw, dict):
        raise JudgeFailure("protocol")
    probabilities = _probabilities(raw.get("probabilities"), request.candidates)
    if request.question is Question.SCORE:
        answer = _score_level(request.candidates, probabilities)
    else:
        answer = raw.get("choice")
        if not isinstance(answer, str):
            raise JudgeFailure("protocol")
    return Verdict(
        answer=answer,
        probabilities=probabilities,
        elapsed_ms=elapsed_ms,
        adapter="jev",
    )


def _probabilities(raw: object, candidates: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise JudgeFailure("protocol")
    numbers: dict[str, float] = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, int | float)
        ):
            raise JudgeFailure("protocol")
        numbers[key] = float(value)
    if numbers and all(key in candidates for key in numbers):
        return numbers
    remapped: dict[str, float] = {}
    for key, value in numbers.items():
        if not key.isdigit():
            raise JudgeFailure("protocol")
        index = int(key)
        if index >= len(candidates):
            raise JudgeFailure("protocol")
        remapped[candidates[index]] = value
    return remapped


def _score_level(candidates: tuple[str, ...], probabilities: Mapping[str, float]) -> str:
    best: str | None = None
    best_probability: float | None = None
    for candidate in candidates:
        probability = probabilities.get(candidate)
        if probability is None:
            continue
        if best_probability is None or probability > best_probability:
            best = candidate
            best_probability = probability
    if best is None:
        raise JudgeFailure("protocol")
    return best
