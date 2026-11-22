"""Shared judge errors. Adapters raise these instead of talking to hooks."""

from __future__ import annotations


class JudgeFailure(Exception):
    """The judge timed out, received an HTTP error, or left the protocol.

    The message must stay free of API keys, user text, and file contents.
    The ledger records the failure action, not this message.
    """
