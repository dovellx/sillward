"""Start tau with directory discovery off and only this extension loaded."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from sillward.config import ConfigError, ModelTarget, load_config

_EXTENSION_OPTIONS = {"-e", "--extension"}
_DROPPED_FLAGS = {"--no-extensions", "--project-extensions"}
_VALUE_OPTIONS = frozenset(
    {
        "--prompt",
        "--provider",
        "--model",
        "-m",
        "--thinking",
        "-t",
        "--base-url",
        "--api-key-env",
        "--timeout-seconds",
        "--max-retries",
        "--max-retry-delay-seconds",
        "--cwd",
        "--mode",
        "--output",
        "-o",
        "--session",
        "--resume",
        "--session-id",
        "--system-prompt",
        "--append-system-prompt",
        "--auto-compact-threshold",
        "--extension",
        "-e",
        "-x",
    }
)
_SKIP_ALWAYS = frozenset({"update", "install", "export"})
_SKIP_ALONE = frozenset({"sessions", "providers", "setup"})
_STOP_FLAGS = frozenset({"--help", "-h", "--version", "-v", "--export"})


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
    A coding session that did not name its own provider or model gets
    ``models.agent`` from the project config appended at the end.
    """
    forwarded = _forwarded(user_args)
    return [
        "--no-extensions",
        "--extension",
        str(extension_file()),
        *forwarded,
        *_agent_model_arguments(forwarded),
    ]


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


@dataclass(frozen=True, slots=True)
class _LaunchScan:
    selected_model: bool
    reads_config: bool
    cwd: str | None


def _agent_model_arguments(args: Sequence[str]) -> list[str]:
    scan = _scan_launch(args)
    if not scan.reads_config or scan.selected_model:
        return []
    target = load_config(project=_project_directory(scan.cwd), environ={}).models.agent
    if target is None:
        return []
    _apply_agent_api_key(target)
    injected: list[str] = []
    if target.provider is not None:
        injected.extend(["--provider", target.provider])
    if target.model is not None:
        injected.extend(["--model", target.model])
    return injected


def _apply_agent_api_key(target: ModelTarget) -> None:
    """Give this process the coding-session key from the project file.

    Tau reads a stored credential before the provider's environment variable.
    Both are set from the file so a previous credential or environment value
    cannot hide the key the project named. The key is not placed on argv.
    """
    if target.api_key is None:
        return
    import os

    from tau_coding.credentials import FileCredentialStore
    from tau_coding.paths import TauPaths
    from tau_coding.provider_config import (
        ProviderConfigError,
        ProviderSettings,
        load_provider_settings,
        provider_settings_path,
    )

    paths = TauPaths()
    try:
        if provider_settings_path(paths).is_file():
            settings = load_provider_settings(paths)
        else:
            settings = ProviderSettings()
        provider = settings.get_provider(target.provider)
    except ProviderConfigError as exc:
        raise ConfigError(str(exc)) from exc
    os.environ[provider.api_key_env] = target.api_key
    if provider.credential_name:
        FileCredentialStore(paths.home / "credentials.json").set(
            provider.credential_name,
            target.api_key,
        )


def _scan_launch(args: Sequence[str]) -> _LaunchScan:
    positionals: list[str] = []
    selected_model = False
    stop = False
    session_marker = False
    cwd: str | None = None
    options_ended = False
    index = 0
    while index < len(args):
        token = args[index]
        if options_ended:
            positionals.append(token)
            index += 1
            continue
        if token == "--":
            options_ended = True
            index += 1
            continue
        if _is_model_selection(token):
            selected_model = True
        if token in _STOP_FLAGS:
            stop = True
        if token in {"-p", "--print"} or token == "--mode" or token.startswith("--mode="):
            session_marker = True
        if token.startswith("--cwd="):
            cwd = token.split("=", 1)[1]
        elif token == "--cwd":
            cwd = args[index + 1] if index + 1 < len(args) else None
        if _consumes_value(token) and index + 1 < len(args):
            index += 2
            continue
        if not token.startswith("-"):
            positionals.append(token)
        index += 1
    return _LaunchScan(
        selected_model=selected_model,
        reads_config=_reads_config(positionals, session_marker=session_marker, stop=stop),
        cwd=cwd,
    )


def _is_model_selection(token: str) -> bool:
    if token == "--models" or token.startswith("--models="):
        return False
    if token == "--provider" or token.startswith("--provider="):
        return True
    if token == "--model" or token.startswith("--model="):
        return True
    return token == "-m" or (token.startswith("-m") and not token.startswith("--"))


def _consumes_value(token: str) -> bool:
    if token.startswith("--") and "=" in token:
        return False
    return token in _VALUE_OPTIONS


def _reads_config(positionals: Sequence[str], *, session_marker: bool, stop: bool) -> bool:
    if stop:
        return False
    if session_marker:
        return True
    if not positionals:
        return True
    command = positionals[0]
    if command in _SKIP_ALWAYS:
        return False
    return not (command in _SKIP_ALONE and len(positionals) == 1)


def _project_directory(cwd: str | None) -> Path:
    if not cwd:
        return Path.cwd()
    path = Path(cwd)
    if path.is_absolute():
        return path
    return Path.cwd() / path


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
    try:
        launch = tau_launch_arguments(args)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    from tau_coding.cli import app as tau_app

    tau_app(args=launch)
