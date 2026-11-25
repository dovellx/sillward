"""Phase 0 exit checks: version output, importable setup, and a single extension."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _declared_versions() -> tuple[str, str]:
    """Read the package version and the tau-ai pin from the project file.

    These are the contract values. The CLI is not asked to compute them.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins = [
        item.split("==", 1)[1]
        for item in project["dependencies"]
        if isinstance(item, str) and item.startswith("tau-ai==")
    ]
    assert len(pins) == 1
    return str(project["version"]), pins[0]


def test_version_prints_own_version_and_pinned_tau_ai() -> None:
    """`sillward --version` prints the package version and the pinned tau-ai version."""
    own, pinned = _declared_versions()
    executable = shutil.which("sillward")
    assert executable is not None

    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.splitlines() == [f"sillward {own}", f"tau-ai {pinned}"]


def test_pytest_can_import_setup() -> None:
    """Tau's entry point imports and registers the parent-loop hooks and agent."""
    from sillward.extension import setup

    events: list[str] = []
    tools: list[str] = []

    class _Api:
        def on(self, event: str, handler: object = None) -> object:
            def decorate(function: object) -> object:
                events.append(event)
                return function

            if handler is None:
                return decorate
            events.append(event)
            return handler

        def register_tool(self, tool: object) -> None:
            tools.append(str(getattr(tool, "name", tool)))

    assert setup(_Api()) is None
    assert events == ["input", "tool_call", "tool_result", "turn_end"]
    assert tools == ["agent"]


def _load_extensions(args: list[str], *, root: Path, cwd: Path):
    from tau_coding.extensions.loader import load_extensions
    from tau_coding.resources import TauResourcePaths

    extra: list[Path] = []
    include_resource_dirs = True
    include_project_dir = False
    expect_path = False
    for arg in args:
        if expect_path:
            extra.append(Path(arg))
            expect_path = False
            continue
        if arg == "--no-extensions":
            include_resource_dirs = False
            continue
        if arg in {"-e", "--extension"}:
            expect_path = True
            continue
        if arg.startswith("--extension="):
            extra.append(Path(arg.split("=", 1)[1]))
            continue
        if arg == "--project-extensions":
            include_project_dir = True
            continue
    assert not expect_path
    return load_extensions(
        TauResourcePaths(
            root=root,
            cwd=cwd,
            agents_root=None,
            project_resources_enabled=True,
        ),
        extra_paths=extra,
        include_resource_dirs=include_resource_dirs,
        include_project_dir=include_project_dir,
        include_user_dir=True,
    )


def test_tau_launch_loads_only_the_sillward_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory discovery stays off, and a user-supplied -e path is not loaded.

    The launch runs in the temporary project. A checkout config would append model flags.
    """
    from sillward.cli import tau_launch_arguments
    from sillward.extension import setup

    user_dir = tmp_path / "extensions"
    user_dir.mkdir()
    (user_dir / "decoy.py").write_text("def setup(tau):\n    return 'decoy'\n", encoding="utf-8")
    project = tmp_path / "project"
    project_dir = project / ".tau" / "extensions"
    project_dir.mkdir(parents=True)
    (project_dir / "project_decoy.py").write_text(
        "def setup(tau):\n    return 'project'\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "other.py"
    explicit.write_text("def setup(tau):\n    return 'other'\n", encoding="utf-8")
    monkeypatch.chdir(project)

    args = tau_launch_arguments(
        [
            "-e",
            str(explicit),
            "--project-extensions",
            "--extension",
            str(explicit),
            f"--extension={explicit}",
            "--print",
            "hi",
        ]
    )
    loaded = _load_extensions(args, root=tmp_path, cwd=project)

    extension_file = Path(setup.__code__.co_filename).resolve()
    assert [item.path.resolve() for item in loaded.extensions if item.path is not None] == [
        extension_file
    ]
    assert loaded.diagnostics == ()
    assert "--project-extensions" not in args
    assert args.count("--extension") == 1
    assert "--print" in args
    assert "hi" in args
    assert "--provider" not in args
    assert "--model" not in args


def _write_agent_model(project: Path, agent: object) -> None:
    destination = project / ".sillward"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "config.json").write_text(
        json.dumps({"models": {"agent": agent}}),
        encoding="utf-8",
    )


def _write_broken_config(project: Path) -> None:
    destination = project / ".sillward"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "config.json").write_text("{", encoding="utf-8")


def test_launch_without_a_config_does_not_pass_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a developer checkout config would append --provider or --model here."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    args = tau_launch_arguments(["--print", "hi"])

    assert args[:2] == ["--no-extensions", "--extension"]
    assert "--print" in args
    assert "hi" in args
    assert "--provider" not in args
    assert "--model" not in args


def test_agent_model_is_appended_after_the_forwarded_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the file's model would leave tau on its default for this session."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "openai", "model": "gpt-5.4"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(["--print", "describe the task"])

    assert "--print" in args
    assert "describe the task" in args
    assert args[-4:] == ["--provider", "openai", "--model", "gpt-5.4"]


def test_agent_model_id_alone_does_not_append_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inventing --provider would pin a provider the file did not name."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"model": "gpt-5.4"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(["--print", "hi"])

    assert args[-2:] == ["--model", "gpt-5.4"]
    assert "--provider" not in args


def test_agent_provider_alone_does_not_append_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inventing --model would replace that provider's default model."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "openai"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(["--print", "hi"])

    assert args[-2:] == ["--provider", "openai"]
    assert "--model" not in args


def test_judge_model_is_not_passed_to_the_coding_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copying models.judge onto the session would change the coding model too."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    destination = project / ".sillward"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text(
        json.dumps({"models": {"judge": {"provider": "openai", "model": "gpt-4.1"}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    args = tau_launch_arguments(["--print", "hi"])

    assert "gpt-4.1" not in args
    assert "--provider" not in args
    assert "--model" not in args


@pytest.mark.parametrize(
    "argv",
    [
        ["--print", "hi", "--model", "gpt-4o"],
        ["--print", "hi", "--model=gpt-4o"],
        ["--print", "hi", "-m", "gpt-4o"],
        ["--print", "hi", "-mgpt-5.4"],
        ["--print", "hi", "--provider", "openai"],
        ["--print", "hi", "--provider=openai"],
    ],
)
def test_an_explicit_model_flag_is_not_replaced_from_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Filling either field from the file would override the model the user typed."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "from-file-provider", "model": "from-file-model"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(argv)

    assert "from-file-provider" not in args
    assert "from-file-model" not in args
    _write_broken_config(project)
    tau_launch_arguments(argv)


def test_system_prompt_text_is_not_a_model_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating the prompt text --model as a flag would drop the file's model."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "openai", "model": "gpt-5.4"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(["--print", "hi", "--system-prompt", "--model"])

    assert args[args.index("--system-prompt") + 1] == "--model"
    assert args[-4:] == ["--provider", "openai", "--model", "gpt-5.4"]


def test_models_switch_on_update_is_not_a_model_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching --models with startswith('--model') would eat update or inject a model."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "from-file-provider", "model": "from-file-model"})
    monkeypatch.chdir(project)

    for argv in (["update", "--models"], ["--models", "update"]):
        args = tau_launch_arguments(argv)
        assert "from-file-model" not in args
        assert "--provider" not in args
    printed = tau_launch_arguments(["--print", "hi", "--models"])
    assert "--models" in printed
    assert printed[-4:] == ["--provider", "from-file-provider", "--model", "from-file-model"]
    _write_broken_config(project)
    for argv in (["update", "--models"], ["--models", "update"]):
        args = tau_launch_arguments(argv)
        assert "--provider" not in args
        assert "--model" not in args


_SKIPPED = (
    ["update"],
    ["update", "later"],
    ["install"],
    ["install", "pkg"],
    ["sessions"],
    ["export"],
    ["export", "session-id"],
    ["providers"],
    ["setup"],
    ["--help"],
    ["-h"],
    ["-v"],
    ["--export"],
    ["--print", "--help"],
)


@pytest.mark.parametrize("argv", _SKIPPED)
def test_skipped_launches_ignore_the_project_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Injecting on update or setup would pass --model into a command that writes tau's home."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "from-file-provider", "model": "from-file-model"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(argv)

    assert "from-file-model" not in args
    assert "from-file-provider" not in args
    _write_broken_config(project)
    broken = tau_launch_arguments(argv)
    assert "--provider" not in broken
    assert "--model" not in broken


@pytest.mark.parametrize(
    "argv",
    [
        ["--print", "update"],
        ["sessions", "extra"],
        ["providers", "extra"],
        ["setup", "extra"],
    ],
)
def test_prompts_that_look_like_commands_still_take_the_agent_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Skipping these would leave the initial TUI or print prompt on tau's default model."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "openai", "model": "gpt-5.4"})
    monkeypatch.chdir(project)

    args = tau_launch_arguments(argv)

    assert all(token in args for token in argv)
    assert args[-4:] == ["--provider", "openai", "--model", "gpt-5.4"]


def test_cwd_selects_the_project_whose_model_is_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Using the process cwd would inject the first project's model into the second."""
    from sillward.cli import tau_launch_arguments

    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_agent_model(first, {"provider": "first-provider", "model": "first-model"})
    _write_agent_model(second, {"provider": "second-provider", "model": "second-model"})
    monkeypatch.chdir(first)

    spaced = tau_launch_arguments(["--print", "hi", "--cwd", str(second)])
    equals = tau_launch_arguments(["--print", "hi", f"--cwd={second}"])
    last = tau_launch_arguments(["--cwd", str(first), "--print", "hi", "--cwd", str(second)])
    assert spaced[-4:] == ["--provider", "second-provider", "--model", "second-model"]
    assert equals[-4:] == ["--provider", "second-provider", "--model", "second-model"]
    assert last[-4:] == ["--provider", "second-provider", "--model", "second-model"]

    monkeypatch.chdir(tmp_path)
    relative = tau_launch_arguments(["--print", "hi", "--cwd", "second"])
    assert relative[-4:] == ["--provider", "second-provider", "--model", "second-model"]

    monkeypatch.chdir(first)
    trailing = tau_launch_arguments(["--cwd", str(second), "--print", "hi", "--cwd"])
    assert trailing[-4:] == ["--provider", "first-provider", "--model", "first-model"]


def test_cwd_without_a_config_does_not_fall_back_to_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back to the current directory would inject a model for the other project."""
    from sillward.cli import tau_launch_arguments

    current = tmp_path / "current"
    empty = tmp_path / "empty"
    _write_agent_model(current, {"provider": "current-provider", "model": "current-model"})
    empty.mkdir()
    monkeypatch.chdir(current)

    args = tau_launch_arguments(["--print", "hi", "--cwd", str(empty)])

    assert "current-model" not in args
    assert "--provider" not in args
    assert "--model" not in args


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--print", "task"],
        ["-p", "task"],
        ["--mode", "rpc"],
        ["--mode=rpc"],
        ["--session", "abc"],
    ],
)
def test_a_session_launch_rejects_a_broken_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Swallowing the parse error would start tau with no project model and no message."""
    from sillward.cli import tau_launch_arguments
    from sillward.config import ConfigError

    project = tmp_path / "project"
    _write_broken_config(project)
    monkeypatch.chdir(project)

    with pytest.raises(ConfigError, match="^project config is not a JSON object$"):
        tau_launch_arguments(argv)


def test_main_exits_on_a_broken_config_without_starting_tau(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Starting tau would hide the config error behind a traceback or a session."""
    from sillward.cli import main

    project = tmp_path / "project"
    _write_broken_config(project)
    monkeypatch.chdir(project)

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tau started")

    monkeypatch.setattr("tau_coding.cli.app", _refuse)

    with pytest.raises(SystemExit) as caught:
        main(["--print", "task"])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.err == "project config is not a JSON object\n"
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_version_does_not_open_a_broken_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reading config before --version would make the version command exit 2."""
    from sillward.cli import main

    project = tmp_path / "project"
    _write_broken_config(project)
    monkeypatch.chdir(project)

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tau started")

    monkeypatch.setattr("tau_coding.cli.app", _refuse)

    with pytest.raises(SystemExit) as caught:
        main(["--version"])

    assert caught.value.code == 0
    assert "project config" not in capsys.readouterr().err


def test_launch_does_not_save_a_default_provider_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisting the injected model would rewrite tau's providers.json."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "openai", "model": "gpt-5.4"})
    monkeypatch.chdir(project)

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("save_default_provider_model was called")

    monkeypatch.setattr("tau_coding.provider_config.save_default_provider_model", _refuse)

    args = tau_launch_arguments(["--print", "hi"])

    assert args[-4:] == ["--provider", "openai", "--model", "gpt-5.4"]


def test_a_bad_mode_overlay_does_not_block_model_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing os.environ into this read would fail startup on a bad mode overlay."""
    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(project, {"provider": "openai", "model": "gpt-5.4"})
    monkeypatch.chdir(project)
    monkeypatch.setenv("SILLWARD_MODE_OVERLAY", "{")

    args = tau_launch_arguments(["--print", "hi"])

    assert args[-4:] == ["--provider", "openai", "--model", "gpt-5.4"]


def test_agent_api_key_replaces_tau_credentials_and_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored credential or DEEPSEEK_API_KEY would keep the project file's key unused."""
    import os

    from tau_coding.credentials import FileCredentialStore
    from tau_coding.provider_config import (
        ProviderSettings,
        openai_compatible_config_from_provider,
    )

    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    key = "sk-from-project-file"
    _write_agent_model(
        project,
        {"provider": "deepseek", "model": "deepseek-v4-pro", "api_key": key},
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("TAU_HOME", str(tmp_path / "tau-home"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    FileCredentialStore().set("deepseek", "sk-old-credential")

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("save_default_provider_model was called")

    monkeypatch.setattr("tau_coding.provider_config.save_default_provider_model", _refuse)

    args = tau_launch_arguments(["--print", "hi"])

    provider = ProviderSettings().get_provider("deepseek")
    runtime = openai_compatible_config_from_provider(
        provider, credential_reader=FileCredentialStore()
    )
    assert runtime.api_key == key
    assert os.environ["DEEPSEEK_API_KEY"] == key
    assert key not in args
    assert args[-4:] == ["--provider", "deepseek", "--model", "deepseek-v4-pro"]


def test_update_does_not_install_the_agent_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installing the key during update would rewrite tau credentials for a non-session command."""
    import os

    from tau_coding.credentials import FileCredentialStore

    from sillward.cli import tau_launch_arguments

    project = tmp_path / "project"
    _write_agent_model(
        project,
        {"provider": "deepseek", "model": "deepseek-v4-pro", "api_key": "sk-from-project-file"},
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("TAU_HOME", str(tmp_path / "tau-home"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")

    tau_launch_arguments(["update"])

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-env"
    assert FileCredentialStore().get("deepseek") is None
