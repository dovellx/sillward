"""Child-session extension. It adds the role's fixed text and does not register tools."""

from __future__ import annotations

from tau_coding.extensions.api import ExtensionAPI

from sillward.roles.definitions import ROLES, child_role


def setup(tau: ExtensionAPI) -> None:
    """Add the role section for the child session that is loading now."""
    name = child_role.get()
    role = ROLES.get(name) if isinstance(name, str) else None
    if role is None:
        return
    tau.add_prompt_section(role.name, role.instructions)
