"""Sprint 16: tests for the plugin loader.

Covers:
- Empty directory → empty registry.
- Plugin with GATE_PROFILES + BPA_RULES loads correctly.
- Broken plugin surfaces as PluginLoadError, doesn't take others down.
- merge_gate_profiles overrides builtins by name.
- The loader is idempotent across calls.
"""

from __future__ import annotations

from pathlib import Path

from powerbi_orchestrator_mcp.plugins import (
    LoadedPlugin,
    PluginLoadError,
    PluginRegistry,
    load_plugins,
    merge_gate_profiles,
)
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
    BUILTIN_PROFILES,
    GateProfile,
)


def _write_plugin(
    directory: Path,
    name: str,
    content: str,
) -> Path:
    path = directory / f"{name}.py"
    path.write_text(content, encoding="utf-8")
    return path


VALID_PLUGIN = """
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
    GateProfile, GateThresholds,
)

GATE_PROFILES = [
    GateProfile(
        name="custom-strict",
        description="custom org rule",
        warning=GateThresholds(max_findings=0, blocking=True),
    ),
]

def _no_underscore_columns(model_state):
    out = []
    for table in model_state.get("tables", []):
        for col in table.get("columns", []):
            if col["name"].startswith("_"):
                out.append({
                    "rule_id": "CUSTOM_NO_LEADING_UNDERSCORE",
                    "severity": "warning",
                    "object_name": f"{table['name']}[{col['name']}]",
                    "object_type": "column",
                    "message": "no leading underscore",
                })
    return out

BPA_RULES = [
    {
        "rule_id": "CUSTOM_NO_LEADING_UNDERSCORE",
        "rule_name": "no leading underscore",
        "severity": "warning",
        "fn": _no_underscore_columns,
    }
]
"""

BROKEN_PLUGIN = """
import this_module_does_not_exist_xyz
"""


class TestLoadPlugins:
    def test_empty_directory(self, tmp_path: Path) -> None:
        registry = load_plugins(plugin_dir=tmp_path)
        assert registry.loaded == []
        assert registry.errors == []
        assert registry.has_errors() is False

    def test_missing_directory(self, tmp_path: Path) -> None:
        # Non-existent dir → empty registry (not an error).
        registry = load_plugins(plugin_dir=tmp_path / "nope")
        assert registry.loaded == []

    def test_loads_valid_plugin(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "custom_rules", VALID_PLUGIN)
        registry = load_plugins(plugin_dir=tmp_path)
        assert len(registry.loaded) == 1
        plugin = registry.loaded[0]
        assert isinstance(plugin, LoadedPlugin)
        assert plugin.name == "custom_rules"
        assert len(plugin.gate_profiles) == 1
        assert plugin.gate_profiles[0].name == "custom-strict"
        assert len(plugin.bpa_rules) == 1
        assert plugin.bpa_rules[0]["rule_id"] == (
            "CUSTOM_NO_LEADING_UNDERSCORE"
        )

    def test_broken_plugin_does_not_take_others_down(
        self, tmp_path: Path
    ) -> None:
        _write_plugin(tmp_path, "good", VALID_PLUGIN)
        _write_plugin(tmp_path, "bad", BROKEN_PLUGIN)
        registry = load_plugins(plugin_dir=tmp_path)
        assert len(registry.loaded) == 1
        assert registry.loaded[0].name == "good"
        assert len(registry.errors) == 1
        assert isinstance(registry.errors[0], PluginLoadError)
        assert registry.errors[0].name == "bad"
        assert registry.has_errors() is True

    def test_ignores_init_py(self, tmp_path: Path) -> None:
        (tmp_path / "__init__.py").write_text("", encoding="utf-8")
        _write_plugin(tmp_path, "custom_rules", VALID_PLUGIN)
        registry = load_plugins(plugin_dir=tmp_path)
        assert len(registry.loaded) == 1
        assert all(
            p.name != "__init__" for p in registry.loaded
        )


class TestInvalidBpaRules:
    def test_missing_rule_id_caught(
        self, tmp_path: Path
    ) -> None:
        bad = """
from powerbi_orchestrator_mcp.validation.pre_deploy_gate import GateProfile
GATE_PROFILES = []
BPA_RULES = [
    {"rule_name": "x", "severity": "warning", "fn": lambda m: []}
]
"""
        _write_plugin(tmp_path, "missing_id", bad)
        registry = load_plugins(plugin_dir=tmp_path)
        assert registry.loaded == []
        assert len(registry.errors) == 1
        assert "rule_id" in registry.errors[0].error

    def test_non_callable_fn_caught(
        self, tmp_path: Path
    ) -> None:
        bad = """
GATE_PROFILES = []
BPA_RULES = [
    {"rule_id": "X", "rule_name": "x", "severity": "warning", "fn": "not a fn"}
]
"""
        _write_plugin(tmp_path, "bad_fn", bad)
        registry = load_plugins(plugin_dir=tmp_path)
        assert registry.loaded == []
        assert len(registry.errors) == 1
        assert "callable" in registry.errors[0].error

    def test_wrong_gate_profile_type_caught(
        self, tmp_path: Path
    ) -> None:
        bad = """
GATE_PROFILES = [{"not": "a GateProfile"}]
BPA_RULES = []
"""
        _write_plugin(tmp_path, "wrong_type", bad)
        registry = load_plugins(plugin_dir=tmp_path)
        assert registry.loaded == []
        assert "GateProfile" in registry.errors[0].error


class TestMergeGateProfiles:
    def test_overrides_by_name(self) -> None:
        registry = PluginRegistry(
            loaded=[
                LoadedPlugin(
                    name="override",
                    path=Path("/tmp/x.py"),
                    gate_profiles=[
                        GateProfile(
                            name="standard",
                            description="overridden",
                        )
                    ],
                )
            ]
        )
        merged = merge_gate_profiles(
            registry, dict(BUILTIN_PROFILES)
        )
        # The plugin overrode 'standard'.
        assert merged["standard"].description == "overridden"
        # Other builtins are still there.
        assert "strict" in merged
        assert "relaxed" in merged

    def test_no_plugins_returns_builtin_copy(self) -> None:
        registry = PluginRegistry()
        merged = merge_gate_profiles(registry, dict(BUILTIN_PROFILES))
        assert set(merged.keys()) == set(BUILTIN_PROFILES.keys())


class TestPluginRuleInvocation:
    """Exercise the loaded callable end-to-end on a synthetic model."""

    def test_rule_fn_returns_findings(
        self, tmp_path: Path
    ) -> None:
        _write_plugin(tmp_path, "custom_rules", VALID_PLUGIN)
        registry = load_plugins(plugin_dir=tmp_path)
        assert len(registry.loaded) == 1

        rule = registry.loaded[0].bpa_rules[0]
        model_state = {
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {"name": "_internal"},
                        {"name": "Amount"},
                    ],
                }
            ]
        }
        findings = rule["fn"](model_state)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == (
            "CUSTOM_NO_LEADING_UNDERSCORE"
        )
        assert findings[0]["object_name"] == "Sales[_internal]"
