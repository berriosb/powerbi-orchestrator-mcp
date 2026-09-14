"""Plugin system for custom BPA rules + custom gate profiles (v1.9.0).

Plugins are Python modules discovered from a configurable directory
(default ``~/.powerbi-orchestrator-mcp/plugins/``). Each plugin module
can define:

- ``GATE_PROFILES``: list[GateProfile] — adds to / overrides the
  built-in profile dictionary by ``.name``.
- ``BPA_RULES``: list[dict] — custom BPA rules. Each rule has
  ``rule_id``, ``rule_name``, ``severity`` (error | warning | info),
  and a Python callable ``check(model_state) -> list[BpaFinding]``
  under ``fn`` that returns zero-or-more findings.

A plugin file looks like::

    # ~/.powerbi-orchestrator-mcp/plugins/my_org_rules.py
    from powerbi_orchestrator_mcp.validation.pre_deploy_gate import (
        GateProfile, GateThresholds,
    )

    GATE_PROFILES = [
        GateProfile(
            name="acme-strict",
            description="Acme prod: no warnings allowed.",
            warning=GateThresholds(max_findings=0, blocking=True),
        ),
    ]

    def _no_underscore_columns(model_state):
        findings = []
        for table in model_state.get("tables", []):
            for col in table.get("columns", []):
                if col["name"].startswith("_"):
                    findings.append({
                        "rule_id": "ACME_NO_LEADING_UNDERSCORE",
                        "severity": "warning",
                        "object_name": f"{table['name']}[{col['name']}]",
                        "object_type": "column",
                        "message": "Acme naming convention forbids leading _",
                    })
        return findings

    BPA_RULES = [
        {
            "rule_id": "ACME_NO_LEADING_UNDERSCORE",
            "rule_name": "Acme: no leading underscore in column names",
            "severity": "warning",
            "fn": _no_underscore_columns,
        }
    ]

To install::

    mkdir -p ~/.powerbi-orchestrator-mcp/plugins
    cp my_org_rules.py ~/.powerbi-orchestrator-mcp/plugins/
    # restart the orchestrator

The plugin loader surfaces warnings + errors via the
``powerbi_health`` tool (``warnings`` + ``remediation`` fields) so the
LLM can show users "your plugin X failed to load" with a hint.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from powerbi_orchestrator_mcp.validation.pre_deploy_gate import GateProfile

# Default plugin directory (next to audit + plan DBs).
PLUGIN_DIR = Path.home() / ".powerbi-orchestrator-mcp" / "plugins"


@dataclass
class LoadedPlugin:
    """A successfully-loaded plugin module."""

    name: str
    path: Path
    gate_profiles: list[GateProfile] = field(default_factory=list)
    bpa_rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PluginLoadError:
    """A plugin that failed to load."""

    name: str
    path: Path
    error: str


@dataclass
class PluginRegistry:
    """Aggregated registry of loaded plugins + load errors.

    Usage::

        registry = load_plugins()
        # In PreDeployGate:
        for profile in registry.all_gate_profiles():
            BUILTIN_PROFILES[profile.name] = profile
        # In BPA runner:
        for rule in registry.all_bpa_rules():
            findings.extend(rule["fn"](model_state))
    """

    loaded: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginLoadError] = field(default_factory=list)

    def all_gate_profiles(self) -> list[GateProfile]:
        out: list[GateProfile] = []
        for p in self.loaded:
            out.extend(p.gate_profiles)
        return out

    def all_bpa_rules(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in self.loaded:
            out.extend(p.bpa_rules)
        return out

    def has_errors(self) -> bool:
        return len(self.errors) > 0


def _load_one(path: Path) -> LoadedPlugin | PluginLoadError:
    """Load a single plugin module from ``path``."""
    try:
        spec = importlib.util.spec_from_file_location(
            f"powerbi_plugin_{path.stem}", path
        )
        if spec is None or spec.loader is None:
            return PluginLoadError(
                name=path.stem,
                path=path,
                error="could not create module spec",
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # cache
        spec.loader.exec_module(module)

        gate_profiles = list(getattr(module, "GATE_PROFILES", []) or [])
        bpa_rules = list(getattr(module, "BPA_RULES", []) or [])

        # Light validation.
        for gp in gate_profiles:
            if not isinstance(gp, GateProfile):
                return PluginLoadError(
                    name=path.stem,
                    path=path,
                    error=(
                        "GATE_PROFILES must contain GateProfile "
                        "instances"
                    ),
                )
        for rule in bpa_rules:
            for key in ("rule_id", "rule_name", "severity", "fn"):
                if key not in rule:
                    return PluginLoadError(
                        name=path.stem,
                        path=path,
                        error=f"BPA_RULES entry missing {key!r}",
                    )
            if not callable(rule["fn"]):
                return PluginLoadError(
                    name=path.stem,
                    path=path,
                    error=f"BPA_RULES entry fn for {rule.get('rule_id')!r} "
                    "is not callable",
                )

        return LoadedPlugin(
            name=path.stem,
            path=path,
            gate_profiles=gate_profiles,
            bpa_rules=bpa_rules,
        )
    except Exception as exc:  # noqa: BLE001
        return PluginLoadError(
            name=path.stem, path=path, error=str(exc)
        )


def load_plugins(plugin_dir: Path | None = None) -> PluginRegistry:
    """Discover + load all plugins from ``plugin_dir``.

    If the directory doesn't exist, returns an empty registry.
    Failures are collected, not raised: one broken plugin doesn't
    take the others down.
    """
    target = plugin_dir or PLUGIN_DIR
    registry = PluginRegistry()
    if not target.exists():
        return registry
    for path in sorted(target.glob("*.py")):
        if path.name == "__init__.py":
            continue
        result = _load_one(path)
        if isinstance(result, LoadedPlugin):
            registry.loaded.append(result)
        else:
            registry.errors.append(result)
    return registry


def merge_gate_profiles(
    registry: PluginRegistry,
    builtin: dict[str, GateProfile],
) -> dict[str, GateProfile]:
    """Merge plugin profiles into a copy of ``builtin``.

    Plugin profiles with the same name override builtins.
    """
    merged = dict(builtin)
    for profile in registry.all_gate_profiles():
        merged[profile.name] = profile
    return merged


__all__ = [
    "LoadedPlugin",
    "PLUGIN_DIR",
    "PluginLoadError",
    "PluginRegistry",
    "load_plugins",
    "merge_gate_profiles",
]
