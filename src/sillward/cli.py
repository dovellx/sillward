"""Start tau with directory discovery off and only this extension loaded."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

_EXTENSION_OPTIONS = {"-e", "--extension"}
_DROPPED_FLAGS = {"--no-extensions", "--project-extensions"}


def extension_file() -> Path:
    """Return the installed ``extension.py`` tau should load."""
    import sillward.extension as extension_module

    file = extension_module.__file__
    if file is None:
        raise RuntimeError("sillward extension has no source file")
    return Path(file).resolve()


def tau_launch_arguments(user_args: Sequence[str]) -> list[str]:
    """Build the argv tau receives.

    Directory discovery is forced off. Explicit extension paths from the user
    are dropped, and the only extension path is this package's ``extension.py``.
    Every other argument is forwarded unchanged.
    """
    return ["--no-extensions", "--extension", str(extension_file()), *_forwarded(user_args)]


def _forwarded(user_args: Sequence[str]) -> list[str]:
    forwarded: list[str] = []
    skip_value = False
    for arg in user_args:
        if skip_value:
            skip_value = False
            continue
        if arg in _DROPPED_FLAGS or arg.startswith("--extension="):
            continue
        if arg in _EXTENSION_OPTIONS:
            skip_value = True
            continue
        forwarded.append(arg)
    return forwarded


def print_version() -> None:
    """Print this package's version and the installed tau-ai version."""
    print(f"sillward {version('sillward')}")
    print(f"tau-ai {version('tau-ai')}")


def main(argv: Sequence[str] | None = None) -> None:
    """Print ``--version``, or start tau in-process with only this extension."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print_version()
        raise SystemExit(0)
    from tau_coding.cli import app as tau_app

    tau_app(args=tau_launch_arguments(args))
