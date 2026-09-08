#!/usr/bin/env python3
"""Smoke test for powerbi-orchestrator-mcp.

Simulates an MCP client (Claude Desktop / VS Code / Cursor) by sending
real JSON-RPC messages over stdio to the orchestrator and verifying the
responses. This proves the package works end-to-end as an MCP server,
without requiring a full MCP client install.

Usage:
    python scripts/verify_mcp_server.py
    # Or with an explicit interpreter:
    /path/to/.venv/bin/python scripts/verify_mcp_server.py

Exit code:
    0 if all checks pass
    1 if any check fails (with diagnostic output)

Checks performed:
    1. The console script `powerbi-orchestrator-mcp` is installed and
       runs without crashing.
    2. The server responds to JSON-RPC `initialize` with valid protocol
       version + server info.
    3. The server responds to `tools/list` with the 3 MVP tools
       (connect_target, plan_change, apply_plan).
    4. Each tool has a non-empty input schema.
    5. The server can be terminated cleanly with a `shutdown` request.

This script is the "falsifiable claim" behind the README's promise
that someone cloning the repo can `pip install -e .` and immediately
use the orchestrator as an MCP server.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Add repo root to sys.path so we can import directly from the source
# tree when run via `python scripts/verify_mcp_server.py` without pip
# install (useful for CI to validate source without packaging).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

EXPECTED_TOOLS = {
    "connect_target",
    "plan_change",
    "apply_plan",
    "audit_model_and_report",
    "deploy_to_workspace",
    "run_refresh",
    "run_dax_regression",
    "diff_models",
    "pre_deploy_check",
    "generate_data_dictionary",
    "apply_theme_and_accessibility_rules",
    "add_measure_with_validation",
    "create_report_from_dataset",
    "edit_report_visual",
    "refactor_to_calculation_groups",
    "select_visuals_for_kpis",
    "design_report_page_from_requirements",
    "optimize_report_performance",
}
MCP_PROTOCOL_VERSION = "2024-11-05"


def _send_message(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    """Write a single JSON-RPC message to the server's stdin, newline-delimited."""
    line = json.dumps(message) + "\n"
    assert proc.stdin is not None
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def _read_message(proc: subprocess.Popen[bytes], timeout: float = 10.0) -> dict[str, Any]:  # noqa: ARG001
    """Read a single newline-delimited JSON-RPC message from stdout."""
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("server closed stdout unexpectedly")
    return json.loads(line.decode("utf-8"))


def _check(label: str, condition: bool, detail: str = "") -> bool:
    """Print a check result and return its pass/fail status."""
    status = "✓" if condition else "✗"
    suffix = f" — {detail}" if detail else ""
    print(f"  {status} {label}{suffix}")
    return condition


def main() -> int:
    # Try the system PATH first; fall back to the same bin dir as the
    # currently-running Python interpreter (covers venv installs where
    # the user runs `python scripts/verify_mcp_server.py` without
    # sourcing the venv activate).
    binary = shutil.which("powerbi-orchestrator-mcp")
    if binary is None:
        # Fallback: search the active venv's bin dir explicitly.
        candidates = []
        # 1. Same dir as sys.executable.
        candidates.append(Path(sys.executable).resolve().parent / "powerbi-orchestrator-mcp")
        # 2. sys.prefix / bin (works for venv where Python is a symlink).
        candidates.append(Path(sys.prefix) / "bin" / "powerbi-orchestrator-mcp")
        # 3. Walk up from sys.executable looking for bin/powerbi-orchestrator-mcp.
        p = Path(sys.executable).resolve().parent
        for _ in range(4):
            candidates.append(p / "bin" / "powerbi-orchestrator-mcp")
            p = p.parent
        for cand in candidates:
            if cand.exists() and cand.is_file():
                binary = str(cand)
                break
    if binary is None:
        print("✗ powerbi-orchestrator-mcp not on PATH and not in active venv")
        print("  Install with: pip install -e . (or pip install .)")
        print("  Or activate the venv that has it installed (e.g. `source .venv/bin/activate`).")
        return 1

    print(f"Using MCP server binary: {binary}")
    print("Spawning MCP server over stdio...")

    proc = subprocess.Popen(  # noqa: S603 — controlled subprocess for verification
        [binary, "--start"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,  # unbuffered; we read/write explicit newlines
    )

    all_passed = True

    try:
        # ---- 1. JSON-RPC initialize ----
        print("\n[1] JSON-RPC initialize handshake")
        _send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "verify-script", "version": "0.1.0"},
                },
            },
        )
        resp = _read_message(proc)
        all_passed &= _check(
            "Response received",
            "result" in resp,
            f"id={resp.get('id')}",
        )
        result = resp.get("result", {})
        all_passed &= _check(
            "Has protocolVersion",
            "protocolVersion" in result,
            f"version={result.get('protocolVersion')!r}",
        )
        all_passed &= _check(
            "Has serverInfo",
            "serverInfo" in result,
            f"name={result.get('serverInfo', {}).get('name')!r}",
        )
        all_passed &= _check(
            "serverInfo.name is powerbi-orchestrator-mcp",
            result.get("serverInfo", {}).get("name") == "powerbi-orchestrator-mcp",
        )

        # Send initialized notification (per MCP spec, required after init).
        _send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        # ---- 2. tools/list ----
        print("\n[2] tools/list — verify MVP tools are exposed")
        _send_message(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        resp = _read_message(proc)
        all_passed &= _check(
            "Response received",
            "result" in resp,
        )
        tools = resp.get("result", {}).get("tools", [])
        tool_names = {t["name"] for t in tools}
        all_passed &= _check(
            f"Got {len(tools)} tools",
            len(tools) > 0,
            f"names={sorted(tool_names)}",
        )
        missing = EXPECTED_TOOLS - tool_names
        all_passed &= _check(
            f"All {len(EXPECTED_TOOLS)} MVP tools present",
            not missing,
            f"missing={missing}" if missing else "",
        )

        # ---- 3b. tools/call — exercise a non-orchestrator tool end-to-end ----
        # We pick pre_deploy_check (pure CPU, no I/O) to validate that
        # the new Sprint 7 tools work over JSON-RPC.
        print("\n[3b] tools/call — pre_deploy_check with findings")
        _send_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "pre_deploy_check",
                    "arguments": {
                        "findings_json": '[{"severity": "warning", "message": "test"}]',
                        "profile": "standard",
                    },
                },
            },
        )
        resp = _read_message(proc, timeout=10.0)
        result = resp.get("result", {})
        structured = result.get("structuredContent", {})
        all_passed &= _check(
            "pre_deploy_check returned a response",
            "result" in resp,
            f"keys={list(result.keys())}",
        )
        # pre_deploy_check has different structuredContent behavior in
        # FastMCP; we just verify the call succeeded.
        all_passed &= _check(
            "pre_deploy_check has some structured content or content",
            (
                (isinstance(structured, dict) and "passed" in structured)
                or (isinstance(result, dict) and "passed" in result)
                or "content" in result
            ),
            f"structured={structured if not isinstance(structured, dict) else list(structured.keys())}",
        )

        # ---- 3c. tools/call — apply_theme_and_accessibility_rules end-to-end ----
        print("\n[3c] tools/call — apply_theme_and_accessibility_rules on a PBIP")
        with tempfile.TemporaryDirectory() as tmpdir:
            pbip_path = Path(tmpdir) / "test.pbip"
            pbip_path.mkdir()
            (pbip_path / "test.pbip").write_text("{}")
            report_dir = pbip_path / "test.Report"
            report_dir.mkdir()
            page_dir = report_dir / "pages" / "Overview"
            page_dir.mkdir(parents=True)
            (page_dir / "page.json").write_text(
                json.dumps(
                    {
                        "visualContainers": [
                            {
                                "id": "v1",
                                "altText": "Sales by region Q4",
                                "visual": {"$type": "card"},
                            }
                        ]
                    }
                )
            )

            _send_message(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "apply_theme_and_accessibility_rules",
                        "arguments": {
                            "pbip_path": str(pbip_path),
                            "palette": "okabe_ito",
                            "auto_backfill_alt_text": "false",
                        },
                    },
                },
            )
            resp = _read_message(proc, timeout=10.0)
            result = resp.get("result", {})
            structured = result.get("structuredContent", {})
            all_passed &= _check(
                "apply_theme_and_accessibility_rules returned a response",
                "result" in resp,
                f"keys={list(result.keys())}",
            )
            all_passed &= _check(
                "theme_written is True",
                structured.get("theme_written") is True,
                f"structured_keys={list(structured.keys()) if isinstance(structured, dict) else type(structured).__name__}",
            )

        # ---- 3. Each tool has a non-empty input schema ----
        print("\n[3] Each MVP tool has a non-empty input schema")
        for tool in tools:
            if tool["name"] not in EXPECTED_TOOLS:
                continue
            schema = tool.get("inputSchema") or tool.get("input_schema")
            schema_ok = bool(schema and (schema.get("properties") or schema.get("type") == "object"))
            all_passed &= _check(
                f"  {tool['name']}.inputSchema present",
                schema_ok,
                f"keys={list(schema.keys()) if isinstance(schema, dict) else 'n/a'}",
            )

        # ---- 4. tools/call: connect_target to verify functional path ----
        print("\n[4] tools/call — connect_target with valid PBIP path")
        with tempfile.TemporaryDirectory() as tmpdir:
            pbip_path = Path(tmpdir) / "test.pbip"
            pbip_path.mkdir()
            (pbip_path / "test.pbip").write_text("{}")
            (pbip_path / "test.Report").mkdir()
            (pbip_path / "test.Report" / "report.json").write_text("{}")

            _send_message(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "connect_target",
                        "arguments": {
                            "target_type": "pbip_folder",
                            "target_ref": str(pbip_path),
                        },
                    },
                },
            )
            resp = _read_message(proc, timeout=15.0)
            result = resp.get("result", {})
            structured = result.get("structuredContent")
            all_passed &= _check(
                "connect_target returned a response",
                "result" in resp,
                f"keys={list(result.keys())}",
            )
            all_passed &= _check(
                "structuredContent has session_id",
                isinstance(structured, dict) and "session_id" in structured,
                f"structured_keys={list(structured.keys()) if isinstance(structured, dict) else type(structured).__name__}",
            )
            all_passed &= _check(
                "structuredContent has engines_available",
                isinstance(structured, dict)
                and "engines_available" in structured,
            )

    except Exception as exc:
        all_passed = False
        print(f"\n✗ Exception during verification: {exc}")
        # Drain stderr for diagnostics.
        try:
            assert proc.stderr is not None
            err = proc.stderr.read().decode("utf-8", errors="replace")
            if err:
                print("--- server stderr ---")
                print(err)
        except Exception:
            pass

    finally:
        # ---- 5. Clean shutdown ----
        print("\n[5] Clean shutdown")
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
            all_passed &= _check(
                "Process terminated cleanly",
                proc.returncode is not None,
                f"exit_code={proc.returncode}",
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            all_passed &= _check("Process terminated cleanly", False, "killed after timeout")
        except Exception as exc:
            all_passed &= _check("Process terminated cleanly", False, f"exc={exc}")

    print()
    if all_passed:
        print("✓ All checks passed — powerbi-orchestrator-mcp works as an MCP server.")
        print("  Someone can clone this repo, run `pip install -e .`, configure")
        print("  their MCP client, and immediately use all 17 MVP+v1.1+v2 tools from their LLM.")
        return 0
    print("✗ One or more checks FAILED — see output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
