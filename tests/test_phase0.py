"""Phase 0 exit checks: version output, importable setup, and a single extension."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

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


def test_tau_launch_loads_only_the_sillward_extension(tmp_path: Path) -> None:
    """Directory discovery stays off, and a user-supplied -e path is not loaded."""
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
