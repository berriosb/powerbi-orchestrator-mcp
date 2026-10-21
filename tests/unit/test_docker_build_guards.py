"""Guard rails for the container build.

The image is the delivery form for anyone running the orchestrator in a
container, and it drifts in ways the Python suite cannot see: the lock
file stops matching the project, the uv pin is too old to read the lock,
the documented commands stop being the ones that work.

These are asserted here rather than left to a reviewer to notice.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "Dockerfile"
_DOCKER_MD = _ROOT / "DOCKER.md"
_COMPOSE = _ROOT / "docker-compose.yml"
_GITIGNORE = _ROOT / ".gitignore"
_PYPROJECT = _ROOT / "pyproject.toml"
_LOCK = _ROOT / "uv.lock"


def _project_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    version = data["project"]["version"]
    assert isinstance(version, str)
    return version


class TestTheLockFileIsUsed:
    def test_the_lock_file_is_tracked(self) -> None:
        """An untracked lock cannot be the one the image builds against."""
        ignore_lines = [
            line.strip()
            for line in _GITIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        assert "uv.lock" not in ignore_lines
        assert _LOCK.exists()

    def test_the_lock_file_exists_and_covers_the_project(self) -> None:
        content = _LOCK.read_text(encoding="utf-8")

        assert 'name = "powerbi-orchestrator-mcp"' in content

    def test_the_build_installs_from_the_lock(self) -> None:
        source = _DOCKERFILE.read_text(encoding="utf-8")

        assert "uv.lock" in source, "the Dockerfile does not copy the lock file"
        assert "--locked" in source, "the build does not refuse to drift"

    def test_the_uv_pin_can_read_the_lock(self) -> None:
        """The lock is on a revision an old uv cannot parse.

        That is why the dependency list used to be spelled out by hand
        in the Dockerfile: the pinned uv was too old for the lock. The
        pin and the lock have to move together.
        """
        source = _DOCKERFILE.read_text(encoding="utf-8")
        match = re.search(r"astral-sh/uv:([0-9.]+)", source)
        assert match, "the Dockerfile does not pin a uv version"

        pinned = tuple(int(part) for part in match.group(1).split("."))
        # The lock declares its format revision; anything before
        # revision 1 needs uv 0.5 or newer.
        assert pinned >= (0, 5), f"uv {match.group(1)} is too old for the lock"

    def test_the_project_is_installed_as_a_copy(self) -> None:
        """An editable install points back at the source tree.

        The runtime stage does not carry the source, so an editable
        install leaves an import that cannot resolve.
        """
        source = _DOCKERFILE.read_text(encoding="utf-8")

        assert "--no-editable" in source


class TestTheDocsMatchTheBuild:
    def test_the_documented_tag_is_the_current_version(self) -> None:
        version = _project_version()

        assert f"powerbi-orchestrator-mcp:{version}" in _DOCKER_MD.read_text(encoding="utf-8")
        assert f"powerbi-orchestrator-mcp:{version}" in _COMPOSE.read_text(encoding="utf-8")

    def test_the_documented_cli_command_uses_the_cli_entrypoint(self) -> None:
        """The image's entrypoint is the MCP server.

        `docker run <image> version` therefore starts the server, not
        the CLI, and prints nothing.
        """
        doc = _DOCKER_MD.read_text(encoding="utf-8")

        for line in doc.splitlines():
            if "docker run" in line and " version" in line:
                assert "--entrypoint powerbi-orchestrator" in line, (
                    f"the documented command does not reach the CLI: {line.strip()}"
                )

    def test_the_compose_file_documents_the_same_way_in(self) -> None:
        compose = _COMPOSE.read_text(encoding="utf-8")

        assert "docker compose run --rm --entrypoint powerbi-orchestrator" in compose


class TestCIKeepsItHonest:
    def test_ci_checks_the_lock_is_current(self) -> None:
        verify = (_ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")

        assert "uv lock --check" in verify

    def test_ci_builds_and_checks_the_image(self) -> None:
        nightly = (_ROOT / ".github" / "workflows" / "e2e-nightly.yml").read_text(encoding="utf-8")

        assert "verify_docker_image.py" in nightly
        assert "docker build" in nightly


class TestTheVerificationScriptExists:
    def test_the_script_is_there(self) -> None:
        assert (_ROOT / "scripts" / "verify_docker_image.py").exists()

    @pytest.mark.parametrize(
        "check",
        ["cli.version", "mcp.tools_list"],
    )
    def test_the_script_covers(self, check: str) -> None:
        source = (_ROOT / "scripts" / "verify_docker_image.py").read_text(encoding="utf-8")

        assert check in source
