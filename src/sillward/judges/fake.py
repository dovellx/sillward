"""A judge whose answer is injected by the caller. It never touches the network."""

from __future__ import annotations

from collections.abc import Mapping

from sillward.judges.base import JudgeFailure
from sillward.protocol import Request, Verdict


class FakeJudge:
    """Return one prepared verdict, or raise a prepared failure."""

    name = "fake"

    def __init__(
        self,
        *,
        answer: str | None = None,
        probabilities: Mapping[str, float] | None = None,
        elapsed_ms: float = 0.0,
        failure: BaseException | None = None,
    ) -> None:
        self._answer = answer
        self._probabilities = None if probabilities is None else dict(probabilities)
        self._elapsed_ms = elapsed_ms
        self._failure = failure

    def judge(self, request: Request) -> Verdict:
        """Return the injected verdict, ignoring the request body."""
        del request
        if self._failure is not None:
            raise self._failure
        if self._answer is None or self._probabilities is None:
            raise JudgeFailure("fake judge has no injected answer")
        return Verdict(
            answer=self._answer,
            probabilities=self._probabilities,
            elapsed_ms=self._elapsed_ms,
            adapter=self.name,
        )
