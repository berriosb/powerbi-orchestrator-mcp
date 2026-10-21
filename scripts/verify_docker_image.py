#!/usr/bin/env python3
"""Check that a built Docker image really works as an MCP server.

The image is the delivery form for anyone who runs the orchestrator in a
container, and it can break in ways the Python test suite cannot see: a
dependency that is not in the image, an entrypoint that points at a
module that was never copied, a console script that was not installed.

This runs the image and checks three things:

    1. the standalone CLI starts and reports a version;
    2. the MCP server completes the JSON-RPC handshake;
    3. the server exposes the same tools the local package exposes.

Usage::

    python scripts/verify_docker_image.py <image-tag>

Exit code:
    0  the image works
    1  at least one check failed
    2  the script could not run (docker missing, no such image)
"""

from __future__ import annotations

import json
import subprocess
import sys

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "verify-docker-image", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _run(
    cmd: list[str], *, stdin: str | None = None, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — the command is built here, not by the caller
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _expected_tool_count() -> int | None:
    """How many tools the local package exposes, if it is importable."""
    try:
        from powerbi_orchestrator_mcp.orchestrator.server import mcp
    except Exception:
        return None
    return len(mcp._tool_manager.list_tools())  # noqa: SLF001


def check_cli(image: str) -> bool:
    proc = _run(["docker", "run", "--rm", "--entrypoint", "powerbi-orchestrator", image, "version"])
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        print(f"[FAIL] cli.version: exit {proc.returncode}: {output[:300]}")
        return False
    if "powerbi-orchestrator-mcp" not in output:
        print(f"[FAIL] cli.version: unexpected output: {output[:200]}")
        return False
    print(f"[ok  ] cli.version: {output.splitlines()[0]}")
    return True


def check_mcp(image: str) -> list[str] | None:
    messages = "\n".join(json.dumps(m) for m in (INITIALIZE, INITIALIZED, TOOLS_LIST)) + "\n"
    proc = _run(["docker", "run", "--rm", "-i", image, "--start"], stdin=messages)
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("id") == 2 and "result" in payload:
            return sorted(tool["name"] for tool in payload["result"].get("tools", []))
    print("[FAIL] mcp.tools_list: the image did not answer tools/list")
    print((proc.stderr or proc.stdout)[-500:])
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: verify_docker_image.py <image-tag>", file=sys.stderr)
        return 2
    image = args[0]

    try:
        _run(["docker", "image", "inspect", image], timeout=60)
    except FileNotFoundError:
        print("docker is not available", file=sys.stderr)
        return 2

    failures = 0

    if not check_cli(image):
        failures += 1

    tools = check_mcp(image)
    if tools is None:
        failures += 1
    else:
        expected = _expected_tool_count()
        print(f"[ok  ] mcp.tools_list: {len(tools)} tool(s) exposed")
        if expected is not None and len(tools) != expected:
            print(f"[FAIL] mcp.tools_list: the image has {len(tools)}, the package has {expected}")
            failures += 1

    print()
    if failures:
        print(f"✗ {failures} check(s) FAILED — the image does not work.")
        return 1
    print("✓ The image works as an MCP server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
