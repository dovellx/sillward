"""Look up a point's mode, ask the judge, apply the failure policy, write the ledger."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sillward.config import Config
from sillward.judges.base import JudgeFailure
from sillward.kernel.ledger import Ledger, LedgerEntry
from sillward.kernel.points import blocks_tool
from sillward.protocol import ProtocolFailure, Request, Verdict, check_verdict


class Judge(Protocol):
    """Anything the kernel can ask. Hooks are not part of this contract."""

    name: str

    def judge(self, request: Request) -> Verdict:
        """Return a verdict or raise JudgeFailure."""


@dataclass(frozen=True, slots=True)
class Decision:
    """What the caller should do with one consultation.

    ``action`` is ``block`` when a tool-blocking point's judge failed or a gate
    refused the call, ``off`` when the point is off or a non-blocking point's
    judge failed, ``execute`` when an active gate let the call proceed,
    ``follow_up`` when an active gate sent the turn back, ``rewrite`` when an
    active gate stored a new frame or replaced tool text, ``capture`` when an
    active memory point wrote a lesson, ``recall`` when active recall selected
    a lesson into the turn, ``discard`` when a child write was dropped without
    asking the judge, and ``unchanged`` when a verdict was recorded without
    changing behavior.
    """

    point: str
    mode: str
    action: str
    answer: str | None


@dataclass(frozen=True, slots=True)
class Consultation:
    """A judge result that has not been written to the ledger yet."""

    request: Request
    mode: str
    verdict: Verdict | None
    failure_action: str
    adapter: str | None


class Kernel:
    """Ask the judge and append one ledger row. It does not register tau hooks."""

    def __init__(self, *, config: Config, judge: Judge, project: Path) -> None:
        self._config = config
        self._judge = judge
        self._ledger = Ledger(project, record_state=config.record_state)

    def consult(self, request: Request) -> Decision:
        """Consult one point and append exactly one ledger row.

        Gates that still have to ask a person use :meth:`open` and
        :meth:`commit` so the row's action is the action they actually took.
        """
        opened = self.open(request)
        if opened.verdict is None:
            return self.commit(opened, action=opened.failure_action)
        return self.commit(opened, action="unchanged")

    def open(self, request: Request) -> Consultation:
        """Resolve the mode and the verdict without writing the ledger."""
        mode = self._config.mode(request.point)
        if mode == "off":
            return Consultation(
                request=request,
                mode=mode,
                verdict=None,
                failure_action="off",
                adapter=None,
            )
        try:
            verdict = self._judge.judge(request)
            check_verdict(request, verdict)
        except (JudgeFailure, ProtocolFailure):
            action = "block" if blocks_tool(request.point) else "off"
            return Consultation(
                request=request,
                mode=mode,
                verdict=None,
                failure_action=action,
                adapter=self._judge.name,
            )
        return Consultation(
            request=request,
            mode=mode,
            verdict=verdict,
            failure_action="",
            adapter=verdict.adapter,
        )

    def commit(self, consultation: Consultation, *, action: str) -> Decision:
        """Append one ledger row for a consultation opened earlier."""
        verdict = consultation.verdict
        if verdict is None:
            return self._finish(
                consultation.request,
                mode=consultation.mode,
                action=action,
                answer=None,
                probabilities=None,
                elapsed_ms=None,
                adapter=consultation.adapter,
            )
        return self._finish(
            consultation.request,
            mode=consultation.mode,
            action=action,
            answer=verdict.answer,
            probabilities=verdict.probabilities,
            elapsed_ms=verdict.elapsed_ms,
            adapter=verdict.adapter,
        )

    def discard(self, request: Request) -> Decision:
        """Append a discard row. The judge is not asked."""
        return self._finish(
            request,
            mode=self._config.mode(request.point),
            action="discard",
            answer=None,
            probabilities=None,
            elapsed_ms=None,
            adapter=None,
        )

    def _finish(
        self,
        request: Request,
        *,
        mode: str,
        action: str,
        answer: str | None,
        probabilities: Mapping[str, float] | None,
        elapsed_ms: float | None,
        adapter: str | None,
    ) -> Decision:
        stored = None if probabilities is None else dict(probabilities)
        self._ledger.append(
            LedgerEntry(
                point=request.point,
                answer=answer,
                probabilities=stored,
                elapsed_ms=elapsed_ms,
                mode=mode,
                adapter=adapter,
                action=action,
            ),
            request,
        )
        return Decision(point=request.point, mode=mode, action=action, answer=answer)
