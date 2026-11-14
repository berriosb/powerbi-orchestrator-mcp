"""ISO 9001/14001/45001 §7.5 handover & audit rotation helpers."""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from powerbi_orchestrator_mcp import __version__
from powerbi_orchestrator_mcp.orchestrator.audit import AuditLog
from powerbi_orchestrator_mcp.orchestrator.paths import get_orchestrator_home
from powerbi_orchestrator_mcp.orchestrator.readiness import collect_readiness
from powerbi_orchestrator_mcp.tools._tabular_io import compute_file_sha256, escape_excel_formula
from powerbi_orchestrator_mcp.validation.path_safety import (
    validate_safe_pbip_path,
    validate_write_path,
)

# Relative paths inside get_orchestrator_home() (real layout + legacy flat fallback).
_HOME_BACKUP_CANDIDATES: list[tuple[str, str]] = [
    ("audit/audit.db", "audit/audit.db"),
    ("audit/audit.db-wal", "audit/audit.db-wal"),
    ("audit/audit.db-shm", "audit/audit.db-shm"),
    ("audit/.audit_anchor", "audit/.audit_anchor"),
    ("plans/plans.db", "plans/plans.db"),
    # Legacy flat fallbacks (kept for backward compatibility in custom fixtures).
    ("audit.db", "audit.db"),
    ("audit.db-wal", "audit.db-wal"),
    ("audit.db-shm", "audit.db-shm"),
    (".audit_anchor", ".audit_anchor"),
    ("plans.db", "plans.db"),
]


_ROTATE_THREAD_LOCK = threading.Lock()
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_sqlite_file(path: Path) -> bool:
    if path.suffix != ".db" or not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            return fh.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def _copy_backup_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _is_sqlite_file(src):
        if dst.exists():
            dst.unlink()
        with (
            contextlib.closing(sqlite3.connect(str(src))) as src_conn,
            contextlib.closing(sqlite3.connect(str(dst))) as dst_conn,
        ):
            src_conn.backup(dst_conn)
        return
    shutil.copy2(src, dst)


@contextlib.contextmanager
def _audit_rotation_lock(lock_dir: Path) -> Iterator[None]:
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".audit_rotate.lock"
    with _ROTATE_THREAD_LOCK, lock_path.open("a+b") as lock_fh:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def export_audit_bundle(
    dest_dir: str,
    *,
    include_key: bool = False,
    rotate: bool = False,
    retention_days: int | None = None,
) -> dict[str, Any]:
    """Export audit/plan databases with SHA-256 manifest, optional chain rollover, and pruning."""
    dest = validate_write_path(dest_dir, must_exist=False)
    dest.mkdir(parents=True, exist_ok=True)
    home = get_orchestrator_home()

    candidates = list(_HOME_BACKUP_CANDIDATES)
    if include_key:
        candidates.append((".audit_key", ".audit_key"))

    copied: dict[str, dict[str, Any]] = {}
    for rel_src, rel_dst in candidates:
        src = home / rel_src
        if not src.exists() or not src.is_file():
            continue
        out_file = dest / rel_dst
        _copy_backup_file(src, out_file)
        digest = hashlib.sha256(out_file.read_bytes()).hexdigest()
        copied[rel_dst] = {"bytes": out_file.stat().st_size, "sha256": digest}

    rotated = False
    rollover_tip: str | None = None
    if rotate:
        active_db = home / "audit" / "audit.db"
        if active_db.exists():
            with _audit_rotation_lock(active_db.parent):
                audit = AuditLog(db_path=active_db)
                verify = audit.verify()
                if not verify.valid:
                    raise RuntimeError(
                        f"refusing audit rotation: chain integrity invalid ({verify.error_message})"
                    )
                conn = audit._connect()
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    last_row = conn.execute(
                        "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    tip_hash = str(last_row[0]) if last_row else "genesis"
                finally:
                    conn.close()
                archived_db_sha = compute_file_sha256(active_db)

                stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
                sealed_dir = dest / f"audit-archive-{stamp}"
                sealed_dir.mkdir(parents=True, exist_ok=True)
                for fname in ("audit.db", "audit.db-wal", "audit.db-shm", ".audit_anchor"):
                    cand = active_db.parent / fname
                    if cand.exists() and cand.is_file():
                        shutil.move(str(cand), str(sealed_dir / fname))
                fresh = AuditLog(db_path=active_db)
                fresh_entry = fresh.insert(
                    tool_name="system:audit_rotate",
                    tool_args={"archive_dir": str(sealed_dir)},
                    result_status="success",
                    payload={
                        "rotated_from_sha256": archived_db_sha,
                        "previous_tip_hash": tip_hash,
                        "previous_entries": verify.total_rows,
                    },
                )
                rotated = True
                rollover_tip = fresh_entry.row_hash

    pruned_archives: list[str] = []
    if retention_days is not None and retention_days > 0:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=retention_days)
        for child in sorted(dest.iterdir()):
            if not child.is_dir() or not child.name.startswith("audit-archive-"):
                continue
            mtime = dt.datetime.fromtimestamp(child.stat().st_mtime, tz=dt.UTC)
            if mtime < cutoff:
                shutil.rmtree(child)
                pruned_archives.append(child.name)

    manifest = {
        "exported_at": dt.datetime.now(dt.UTC).isoformat(),
        "version": __version__,
        "source_home": str(home),
        "include_key": include_key,
        "rotated": rotated,
        "rollover_tip_hash": rollover_tip,
        "pruned_archives": pruned_archives,
        "files": copied,
    }
    manifest_path = dest / "audit-export-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def generate_iso_handoff(
    output_dir: str,
    *,
    solution_name: str,
    owner: str,
    approver: str = "Jefatura de Procesos",
    catalog_path: str | None = None,
    pbip_path: str | None = None,
    version: str = "1.0.0",
) -> dict[str, Any]:
    """Generate ISO 9001/14001/45001 §7.5 handover markdown + document control register."""
    out = validate_write_path(output_dir, must_exist=False)
    out.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()

    doc_rows: list[dict[str, str]] = []
    kpi_summary_lines: list[str] = []
    if catalog_path:
        safe_cat = validate_safe_pbip_path(catalog_path, must_be_dir=False)
        raw_cat = yaml.safe_load(safe_cat.read_text(encoding="utf-8")) or {}
        cat_ver = str(raw_cat.get("version") or version)
        cat_approver = str(raw_cat.get("approver") or approver)
        kpis = raw_cat.get("kpis") or []
        for idx, item in enumerate(kpis, start=1):
            kpi_id = str(item.get("id", f"kpi-{idx}"))
            kpi_owner = str(item.get("owner") or owner)
            period = str(item.get("periodicity") or "monthly")
            kpi_summary_lines.append(
                f"- `{kpi_id}` ({item.get('area', '')}, {period}): dueño `{kpi_owner}`"
            )
            doc_rows.append(
                {
                    "codigo": f"SIG-KPI-{idx:03d}",
                    "titulo": f"Definición KPI: {item.get('name', kpi_id)}",
                    "version": cat_ver,
                    "responsable": kpi_owner,
                    "aprobador": cat_approver,
                    "periodicidad": period,
                    "estado": "Vigente",
                    "fecha_revision": today,
                    "ruta_controlada": str(safe_cat),
                    "sha256": compute_file_sha256(safe_cat)[:16],
                }
            )

    pbip_summary = "No especificado"
    if pbip_path:
        safe_pbip = validate_safe_pbip_path(pbip_path, must_be_dir=True)
        pbip_files = sorted(str(p.relative_to(safe_pbip)) for p in safe_pbip.glob("*.pbip"))
        pbip_summary = (
            f"`{safe_pbip.name}` (artefactos: {', '.join(pbip_files) or 'estructura PBIP'})"
        )
        first_pbip = safe_pbip / pbip_files[0] if pbip_files else None
        doc_rows.append(
            {
                "codigo": "SIG-MOD-001",
                "titulo": f"Modelo/Reporte Power BI: {solution_name}",
                "version": version,
                "responsable": owner,
                "aprobador": approver,
                "periodicidad": "Por cambio",
                "estado": "Vigente",
                "fecha_revision": today,
                "ruta_controlada": str(safe_pbip),
                "sha256": compute_file_sha256(first_pbip)[:16] if first_pbip else "dir",
            }
        )

    audit_status = AuditLog().verify()
    readiness = collect_readiness()
    readiness_lines = [
        f"- {'OK' if f.ok else 'ATENCIÓN'} `{f.name}`: {f.detail}" for f in readiness
    ]

    handoff_md = "\n".join(
        [
            f"# Ficha Operativa de Solución — {solution_name}",
            "",
            f"- **Versión:** {version}",
            f"- **Fecha de emisión:** {today}",
            f"- **Responsable operativo:** {owner}",
            f"- **Aprobador (ISO §7.5):** {approver}",
            f"- **Paquete runtime:** `powerbi-orchestrator-mcp` v{__version__}",
            f"- **Proyecto PBIP:** {pbip_summary}",
            "",
            "## 1. Propósito y Alcance",
            "Automatización determinista de cálculo de indicadores, generación de entregables "
            "Excel/PDF y trazabilidad auditable con cero dependencia de una sola persona.",
            "",
            "## 2. Catálogo de KPIs Cubiertos",
            *(kpi_summary_lines or ["- Sin catálogo YAML adjunto en esta emisión."]),
            "",
            "## 3. Evidencia de Auditoría y Seguridad",
            f"- **Cadena HMAC local:** `valid={audit_status.valid}` "
            f"(`{audit_status.total_rows}` registros)",
            "- **Variables exigidas en producción:** `PBI_STRICT_COMPLIANCE=1`, "
            "`PBI_ORCHESTRATOR_AUDIT_SECRET`, `PBI_EMAIL_ALLOWED_DOMAINS`.",
            "",
            "### Diagnóstico de Readiness",
            *readiness_lines,
            "",
            "## 4. Respaldo y Recuperación (< 15 min)",
            "1. Respaldo periódico: `powerbi-orchestrator audit-export <destino_respaldo> --rotate --retention-days 365`",
            "2. Verificación post-incidente: `powerbi-orchestrator audit-verify`",
            "3. Ejecución autónoma de reportes: `powerbi-orchestrator run-report --config <job.yaml> --live`",
            "",
        ]
    )

    handoff_path = out / "SOLUTION-HANDOFF.md"
    handoff_path.write_text(handoff_md, encoding="utf-8")

    # Add handoff document itself into ISO 7.5 register.
    doc_rows.insert(
        0,
        {
            "codigo": "SIG-SOP-001",
            "titulo": f"Ficha Operativa: {solution_name}",
            "version": version,
            "responsable": owner,
            "aprobador": approver,
            "periodicidad": "Trimestral / por cambio",
            "estado": "Vigente",
            "fecha_revision": today,
            "ruta_controlada": str(handoff_path),
            "sha256": compute_file_sha256(handoff_path)[:16],
        },
    )

    headers = [
        "codigo",
        "titulo",
        "version",
        "responsable",
        "aprobador",
        "periodicidad",
        "estado",
        "fecha_revision",
        "ruta_controlada",
        "sha256",
    ]
    csv_path = out / "iso-7.5-document-control.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in doc_rows:
            writer.writerow({k: escape_excel_formula(row[k]) for k in headers})

    xlsx_path = out / "iso-7.5-document-control.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Control_Documental_7_5"
    ws.append(headers)
    for row in doc_rows:
        ws.append([escape_excel_formula(row[k]) for k in headers])
    wb.save(xlsx_path)

    return {
        "status": "ok",
        "handoff_path": str(handoff_path),
        "control_csv_path": str(csv_path),
        "control_xlsx_path": str(xlsx_path),
        "documents_count": len(doc_rows),
        "audit_valid": audit_status.valid,
    }


__all__ = ["export_audit_bundle", "generate_iso_handoff"]
