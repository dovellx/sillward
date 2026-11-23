"""swarm.routing. Shadow records the answer and keeps the parent's role."""

from __future__ import annotations

from sillward.gates.support import choice, commit_gate
from sillward.kernel.runner import Kernel
from sillward.protocol import Verdict

_CANDIDATES = ("implementer", "reviewer", "explorer")


def choose_role(kernel: Kernel, requested: str, *, threshold: float) -> str:
    """Return the role to spawn.

    Shadow keeps ``requested``. Active replaces it only when the judge names
    another role and that answer's probability reaches ``threshold``.
    """
    opened = kernel.open(
        choice(
            "swarm.routing",
            _CANDIDATES,
            summary="role",
            state={"requested": requested},
        )
    )
    verdict = opened.verdict
    if (
        verdict is not None
        and opened.mode == "active"
        and verdict.answer in _CANDIDATES
        and verdict.answer != requested
        and _probability(verdict) >= threshold
    ):
        commit_gate(kernel, opened, "execute")
        return verdict.answer
    commit_gate(kernel, opened, "unchanged")
    return requested


def _probability(verdict: Verdict) -> float:
    value = verdict.probabilities.get(verdict.answer)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)
