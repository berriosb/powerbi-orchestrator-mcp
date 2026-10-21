#!/usr/bin/env python3
"""Read-only preflight for validating against a real Fabric workspace.

The unit and integration suites never call a live API: every cloud path is
exercised with test doubles or in ``dry_run``. This script closes that gap
by checking the things that only fail against a real tenant — service
principal permissions, API shape drift, and DAX round-tripping.

It is deliberately read-only. It never creates, updates, refreshes or
deletes anything, so it is safe to point at a production workspace. The
mutating capabilities are validated separately, by hand, with
``docs/pilot-validation.md``.

Usage::

    python scripts/pilot_check.py
    python scripts/pilot_check.py --workspace-id <id> --dataset-id <id>

Authentication follows the orchestrator's own rules: interactive browser
login by default, or ``PBI_AUTH_MODE=service_principal`` with
``AZURE_TENANT_ID`` / ``AZURE_CLIENT_ID`` / ``AZURE_CLIENT_SECRET``.

Exit code:
    0  every check passed
    1  at least one check failed
    2  the script could not run (bad arguments, auth setup)

Reading the output:

    [ok]     the capability works against this tenant
    [FAIL]   the capability is missing or the API disagreed with our model;
             the detail line says which one
    [SKIP]   not applicable to this run (for example, no dataset id given)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

OK = "ok"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    checks: list[Check]

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name=name, status=status, detail=detail))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def render(self) -> str:
        lines = []
        for check in self.checks:
            lines.append(f"[{check.status:4}] {check.name}: {check.detail}")
        passed = len([c for c in self.checks if c.status == OK])
        skipped = len([c for c in self.checks if c.status == SKIP])
        lines.append("")
        lines.append(f"{passed} passed, {len(self.failures)} failed, {skipped} skipped")
        return "\n".join(lines)


def _truncate(text: object, limit: int = 160) -> str:
    """Shorten a message for the report.

    It takes an ``object`` because the callers pass either the API
    response body or the exception itself.
    """
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}..."


async def run_checks(workspace_id: str | None, dataset_id: str | None) -> Report:
    from powerbi_orchestrator_mcp.cloud.auth import AuthConfig, FabricCredential
    from powerbi_orchestrator_mcp.cloud.fabric_client import FabricAPIError, FabricClient

    report = Report(checks=[])

    mode = os.environ.get("PBI_AUTH_MODE", "interactive")
    try:
        config = AuthConfig.from_env_or_args(mode=mode)
        config.validate()
    except Exception as exc:
        report.add("auth.config", FAIL, _truncate(exc))
        return report

    report.add("auth.config", OK, f"auth mode {mode}")

    try:
        client = FabricClient(FabricCredential(config))
    except Exception as exc:
        report.add("auth.credential", FAIL, _truncate(exc))
        return report

    try:
        try:
            workspaces = await client.list_workspaces()
            report.add(
                "auth.token",
                OK,
                f"{len(workspaces)} workspace(s) visible to this identity",
            )
        except FabricAPIError as exc:
            report.add(
                "auth.token",
                FAIL,
                f"{exc.status_code} {_truncate(exc.response_body or exc)}",
            )
            report.add("scope.workspaces.read", FAIL, "could not list workspaces")
            return report
        except Exception as exc:
            report.add("auth.token", FAIL, _truncate(f"{type(exc).__name__}: {exc}"))
            return report

        target = workspace_id
        if target is None and workspaces:
            target = str(workspaces[0].get("id") or "")
            report.add(
                "workspace.select",
                OK,
                f"no --workspace-id given; using the first visible workspace {target}",
            )
        if not target:
            report.add("workspace.resolve", SKIP, "no workspace id available")
            return report

        try:
            workspace = await client.get_workspace(target)
            report.add(
                "workspace.read",
                OK,
                f"{_truncate(workspace.get('displayName') or workspace.get('name') or target)}",
            )
        except FabricAPIError as exc:
            report.add(
                "workspace.read",
                FAIL,
                f"{exc.status_code} {_truncate(exc.response_body or exc)}",
            )
            return report
        except Exception as exc:
            report.add("workspace.read", FAIL, _truncate(f"{type(exc).__name__}: {exc}"))
            return report

        try:
            items = await client.list_items(target)
            report.add("items.read", OK, f"{len(items)} item(s) in the workspace")
        except FabricAPIError as exc:
            report.add(
                "items.read",
                FAIL,
                f"{exc.status_code} {_truncate(exc.response_body or exc)} "
                "(needs the Workspace.Read scope)",
            )

        try:
            datasets = await client.list_datasets(target)
            report.add("datasets.read", OK, f"{len(datasets)} dataset(s) reachable")
        except FabricAPIError as exc:
            report.add(
                "datasets.read",
                FAIL,
                f"{exc.status_code} {_truncate(exc.response_body or exc)} "
                "(needs Dataset.Read scope)",
            )
            datasets = []

        resolved_dataset = dataset_id
        if resolved_dataset is None and datasets:
            resolved_dataset = str(datasets[0].get("id") or "")
            report.add(
                "dataset.select",
                OK,
                f"no --dataset-id given; using dataset {resolved_dataset}",
            )
        if not resolved_dataset:
            report.add("dataset.read", SKIP, "no dataset id available")
            return report

        try:
            await client.get_dataset(target, resolved_dataset)
            report.add("dataset.read", OK, f"dataset {resolved_dataset} is readable")
        except FabricAPIError as exc:
            report.add(
                "dataset.read",
                FAIL,
                f"{exc.status_code} {_truncate(exc.response_body or exc)}",
            )
            return report
        except Exception as exc:
            report.add("dataset.read", FAIL, _truncate(f"{type(exc).__name__}: {exc}"))
            return report

        await _check_dax(client, target, resolved_dataset, report)

        try:
            history = await client.get_refresh_history(target, resolved_dataset)
            report.add(
                "refresh.history.read",
                OK,
                f"{len(history)} refresh record(s) visible",
            )
        except FabricAPIError as exc:
            report.add(
                "refresh.history.read",
                FAIL,
                f"{exc.status_code} {_truncate(exc.response_body or exc)}",
            )
        except Exception as exc:
            report.add(
                "refresh.history.read",
                FAIL,
                _truncate(f"{type(exc).__name__}: {exc}"),
            )
    finally:
        await client.aclose()

    return report


async def _check_dax(
    client: Any,
    workspace_id: str,
    dataset_id: str,
    report: Report,
) -> None:
    from powerbi_orchestrator_mcp.cloud.fabric_client import FabricAPIError

    try:
        payload = await client.execute_queries(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            queries=[{"query": 'EVALUATE ROW("PilotCheck", 1)'}],
        )
    except FabricAPIError as exc:
        report.add(
            "dax.query",
            FAIL,
            f"{exc.status_code} {_truncate(exc.response_body or exc)}",
        )
        return
    except Exception as exc:
        report.add("dax.query", FAIL, _truncate(f"{type(exc).__name__}: {exc}"))
        return

    rows = _count_rows(payload)
    report.add(
        "dax.query",
        OK,
        f"a trivial DAX query returned {rows} row(s)",
    )

    from powerbi_orchestrator_mcp.orchestrator.server import (
        _DAX_DEFAULT_MAX_ROWS,
        _cap_dax_rows,
    )

    capped, truncated = _cap_dax_rows(payload, _DAX_DEFAULT_MAX_ROWS)
    if truncated:
        report.add(
            "dax.row_cap",
            FAIL,
            f"the row cap truncated a {rows}-row response; the default "
            f"cap of {_DAX_DEFAULT_MAX_ROWS} is below this result set",
        )
    else:
        report.add(
            "dax.row_cap",
            OK,
            f"row cap of {_DAX_DEFAULT_MAX_ROWS} left this response untouched",
        )

    if not isinstance(capped, dict) or "results" not in capped:
        report.add(
            "dax.response_shape",
            FAIL,
            "the executeQueries response does not match the shape the "
            "orchestrator expects (missing 'results')",
        )
    else:
        report.add("dax.response_shape", OK, "response shape matches expectations")


def _count_rows(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    total = 0
    for result in payload.get("results", []) or []:
        if not isinstance(result, dict):
            continue
        for table in result.get("tables", []) or []:
            if isinstance(table, dict) and isinstance(table.get("rows"), list):
                total += len(table["rows"])
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only preflight against a real Fabric workspace."
    )
    parser.add_argument("--workspace-id", default=None, help="Fabric workspace id")
    parser.add_argument("--dataset-id", default=None, help="Dataset id to query")
    parser.add_argument("--quiet", action="store_true", help="only print failures and the summary")
    args = parser.parse_args(argv)

    print("powerbi-orchestrator-mcp — pilot preflight (read-only)")
    print("This script never creates, updates, refreshes or deletes anything.")
    print()

    report = asyncio.run(run_checks(args.workspace_id, args.dataset_id))

    if args.quiet:
        for check in report.failures:
            print(f"[{check.status:4}] {check.name}: {check.detail}")
        passed = len([c for c in report.checks if c.status == OK])
        skipped = len([c for c in report.checks if c.status == SKIP])
        print(f"\n{passed} passed, {len(report.failures)} failed, {skipped} skipped")
    else:
        print(report.render())

    if report.failures:
        print()
        print(
            "Resolve the failures above before running any mutating tool. "
            "Most [FAIL] lines are missing service-principal permissions, not "
            "orchestrator bugs."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
