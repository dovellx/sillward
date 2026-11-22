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

    ``action`` is ``block`` when a tool-blocking point's judge failed,
    ``off`` when the point is off or a non-blocking point's judge failed,
    and ``unchanged`` when a verdict was recorded and no gate has applied it.
    """

    point: str
    mode: str
    action: str
    answer: str | None


class Kernel:
    """The phase-1 kernel. It does not register tau hooks."""

    def __init__(self, *, config: Config, judge: Judge, project: Path) -> None:
        self._config = config
        self._judge = judge
        self._ledger = Ledger(project, record_state=config.record_state)

    def consult(self, request: Request) -> Decision:
        """Consult one point and append exactly one ledger row."""
        mode = self._config.mode(request.point)
        if mode == "off":
            return self._finish(
                request,
                mode=mode,
                action="off",
                answer=None,
                probabilities=None,
                elapsed_ms=None,
                adapter=None,
            )
        try:
            verdict = self._judge.judge(request)
            check_verdict(request, verdict)
        except (JudgeFailure, ProtocolFailure):
            action = "block" if blocks_tool(request.point) else "off"
            return self._finish(
                request,
                mode=mode,
                action=action,
                answer=None,
                probabilities=None,
                elapsed_ms=None,
                adapter=self._judge.name,
            )
        return self._finish(
            request,
            mode=mode,
            action="unchanged",
            answer=verdict.answer,
            probabilities=verdict.probabilities,
            elapsed_ms=verdict.elapsed_ms,
            adapter=verdict.adapter,
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
