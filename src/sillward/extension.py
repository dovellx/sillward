"""Tau extension entry. Phase 0 registers nothing."""

from __future__ import annotations

from tau_coding.extensions.api import ExtensionAPI


def setup(tau: ExtensionAPI) -> None:
    """Accept tau's extension API and register nothing."""
    return None
