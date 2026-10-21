"""Guard: the README must not document parameters that do not exist.

The README shows the tool calls a reader is expected to paste into an
MCP client. A parameter that was renamed, removed or never existed
makes the example fail at the first call — and the reader has no way to
know whether the tool or the documentation is wrong.

This test parses the tool calls out of the README and checks every
argument against the schema the server actually publishes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator.server import mcp

README = Path(__file__).resolve().parents[2] / "README.md"

# A tool call in the README: the name, then an argument list that may
# span several lines.
_CALL_RE = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*)\("
    r"(?P<args>.*?)"
    r"\)",
    re.DOTALL | re.MULTILINE,
)

# Keywords that are Python, not tool arguments.
_PY_KEYWORDS = {"true", "false", "null", "none"}


def _tool_names() -> set[str]:
    return {tool.name for tool in mcp._tool_manager.list_tools()}  # noqa: SLF001


def _schema_properties(name: str) -> set[str]:
    manager = mcp._tool_manager  # noqa: SLF001
    for tool in manager.list_tools():
        if tool.name == name:
            params = tool.parameters or {}
            return set(params.get("properties", {}))
    return set()


def _readme_calls() -> list[tuple[str, list[str]]]:
    """Every ``tool(arg=...)`` call in the README, with its arguments."""
    text = README.read_text(encoding="utf-8")
    calls: list[tuple[str, list[str]]] = []
    for match in _CALL_RE.finditer(text):
        name = match.group("name")
        if name not in _tool_names():
            # Not a tool call (a Python-ish snippet, a config block).
            continue
        args = [
            arg.strip()
            for arg in match.group("args").split(",")
            if arg.strip() and "=" in arg and not arg.strip().startswith("#")
        ]
        calls.append((name, args))
    return calls


class TestReadmeMatchesTheTools:
    def test_the_readme_has_tool_calls_to_check(self) -> None:
        """If this fails, the parser stopped matching and the guard is blind."""
        calls = _readme_calls()
        assert calls, "no tool calls found in the README"
        assert any(name == "connect_target" for name, _ in calls)

    def test_every_documented_argument_exists(self) -> None:
        unknown: list[str] = []
        for name, args in _readme_calls():
            allowed = _schema_properties(name)
            for arg in args:
                key = arg.split("=", 1)[0].strip()
                if key in _PY_KEYWORDS:
                    continue
                if key not in allowed:
                    unknown.append(f"{name}({key}=...)")

        assert not unknown, f"README documents unknown arguments: {sorted(set(unknown))}"

    def test_the_tools_the_readme_shows_exist(self) -> None:
        """The README must not show a call to a tool that was removed."""
        names = {name for name, _ in _readme_calls()}
        assert names <= _tool_names()


@pytest.mark.parametrize(
    "name",
    [
        "connect_target",
        "plan_change",
        "apply_plan",
        "audit_model_and_report",
        "pre_deploy_check",
        "deploy_to_workspace",
        "run_refresh",
        "apply_theme_and_accessibility_rules",
        "add_measure_with_validation",
    ],
)
def test_the_flow_documents_the_expected_tools(name: str) -> None:
    """The three end-to-end flows must stay complete."""
    assert name in {tool.name for tool in mcp._tool_manager.list_tools()}  # noqa: SLF001
    documented = {n for n, _ in _readme_calls()}
    assert name in documented, f"{name} is not shown in the README flows"
