"""The twelve decision points, their default modes, and failure classes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    """One decision point declared for the kernel."""

    id: str
    blocks_tool: bool
    default_mode: str = "shadow"


def _point(point_id: str, *, blocks_tool: bool) -> Point:
    return Point(id=point_id, blocks_tool=blocks_tool)


# Order is the plan's order. tool.approval and tool.constraint are the only
# points whose judge failure blocks the tool call. Every other failure is
# treated as off. Defaults stay shadow until a person edits project config.
POINTS: dict[str, Point] = {
    point.id: point
    for point in (
        _point("task.frame", blocks_tool=False),
        _point("tool.approval", blocks_tool=True),
        _point("tool.constraint", blocks_tool=True),
        _point("tool.injection", blocks_tool=False),
        _point("tool.admission", blocks_tool=False),
        _point("turn.continue", blocks_tool=False),
        _point("turn.completion", blocks_tool=False),
        _point("memory.recall", blocks_tool=False),
        _point("memory.capture", blocks_tool=False),
        _point("memory.worth", blocks_tool=False),
        _point("swarm.routing", blocks_tool=False),
        _point("swarm.patch", blocks_tool=False),
    )
}


def blocks_tool(point_id: str) -> bool:
    """Return whether a judge failure on this point must block the tool call."""
    return POINTS[point_id].blocks_tool
