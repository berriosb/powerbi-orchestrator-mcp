"""Story variance analysis — Sprint 14.

Compares two rendered PNG pages and reports variance. Built on top of
the deterministic PNG renderer added in Sprint 13; same source page
content yields byte-identical PNGs, so visual regressions are surfaced
as byte mismatches + a robust pixel-level diff (count of differing
bytes per chunk).

This is the v2 implementation noted in the original Sprint 12 outline
of ``audit_report_ux_and_storytelling``: real rendering telemetry is
not available (no Desktop Bridge here), but a deterministic wireframe
renderer + a byte-level diff gives a usable signal for "this page
rendered differently than before", which is what story variance is
about.

For now the helper ships as a standalone utility
(``compute_story_variance``); a follow-up could embed it inside
``audit_report_ux_and_storytelling`` as an opt-in mode.
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# CRC32 (already in zlib) + PNG chunk walking
# ---------------------------------------------------------------------------


@dataclass
class _ChunkInfo:
    """One PNG chunk (header + data + CRC)."""

    type_bytes: bytes
    type: str
    data: bytes
    offset: int
    length: int

    @property
    def crc(self) -> int:
        """Compute the standard CRC32 of ``type + data``."""
        return zlib.crc32(self.type_bytes + self.data) & 0xFFFFFFFF


def _read_png_chunks(data: bytes) -> list[_ChunkInfo]:
    """Walk a PNG byte-stream and return one entry per chunk.

    The 8-byte signature is not included in the output; chunks are
    returned in file order.
    """
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    chunks: list[_ChunkInfo] = []
    cursor = 8
    while cursor < len(data):
        if cursor + 8 > len(data):
            break
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        type_bytes = data[cursor + 4 : cursor + 8]
        chunk_data = data[cursor + 8 : cursor + 8 + length]
        if cursor + 12 + length > len(data):
            break
        chunks.append(
            _ChunkInfo(
                type_bytes=type_bytes,
                type=type_bytes.decode("ascii", errors="replace"),
                data=chunk_data,
                offset=cursor,
                length=length,
            )
        )
        cursor += 12 + length
    return chunks


def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Decode the raw RGB pixels from a (deterministic) PNG.

    Returns (width, height, raw_rgb_bytes).
    """
    chunks = _read_png_chunks(data)
    ihdr = next((c for c in chunks if c.type == "IHDR"), None)
    if ihdr is None:
        raise ValueError("missing IHDR")
    width, height, depth, color_type = struct.unpack(
        ">IIBB", ihdr.data[:10]
    )
    if depth != 8 or color_type != 2:
        raise ValueError(
            f"unsupported PNG (depth={depth}, color_type={color_type}); "
            "only 8-bit RGB is supported"
        )
    idat = next((c for c in chunks if c.type == "IDAT"), None)
    if idat is None:
        raise ValueError("missing IDAT")
    decompressed = zlib.decompress(idat.data)
    # Strip the filter byte per scanline.
    row_size = width * 3 + 1
    if len(decompressed) != row_size * height:
        raise ValueError("decompressed size mismatch")
    pixels = bytearray()
    for j in range(height):
        line_start = j * row_size
        pixels.extend(decompressed[line_start + 1 : line_start + row_size])
    return width, height, bytes(pixels)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class StoryVarianceFinding:
    """One variance finding per page."""

    page_name: str
    status: str  # "unchanged" | "changed" | "added" | "removed"
    pixel_diff_pct: float  # 0.0-100.0
    sha256_baseline: str
    sha256_current: str
    differing_lines: int = 0
    total_lines: int = 0
    note: str = ""


@dataclass
class StoryVarianceResult:
    """Bundle of variances for a multi-page comparison."""

    findings: list[StoryVarianceFinding]
    pages_compared: int
    pages_unchanged: int
    pages_changed: int
    pages_added: int
    pages_removed: int
    overall_variance_pct: float
    warnings: list[str]


def _sha256_png(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _per_pixel_diff(width: int, height: int, baseline: bytes, current: bytes) -> tuple[int, int]:
    """Return (differing_pixel_count, total_pixel_count) by RGB comparison."""
    if len(baseline) != len(current):
        return width * height, width * height
    differing = 0
    for i in range(0, len(baseline), 3):
        if (
            baseline[i] != current[i]
            or baseline[i + 1] != current[i + 1]
            or baseline[i + 2] != current[i + 2]
        ):
            differing += 1
    return differing, width * height


def _decode_or_none(data: bytes, warnings: list[str], label: str) -> tuple[int, int, bytes] | None:
    try:
        return _decode_png(data)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{label}: failed to decode PNG: {exc}")
        return None


def compute_story_variance(
    baseline_dir: str | os.PathLike[str],
    current_dir: str | os.PathLike[str],
    *,
    file_suffix: str = ".png",
    change_threshold_pct: float = 0.1,
) -> StoryVarianceResult:
    """Compare PNGs produced by ``screenshot_report_pages`` between two directories.

    Args:
        baseline_dir: Path to the baseline PNGs (e.g. a checked-in
            directory or a previous CI run).
        current_dir: Path to the current PNGs.
        file_suffix: File suffix to consider; default ``.png``.
        change_threshold_pct: Pixel-diff percentage above which a page
            is classified as ``changed`` rather than noise.

    Returns:
        StoryVarianceResult with per-page findings and an overall
        variance percentage.
    """
    warnings: list[str] = []
    baseline_root = Path(baseline_dir)
    current_root = Path(current_dir)

    baseline_files = sorted(
        p for p in baseline_root.glob(f"*{file_suffix}") if p.is_file()
    )
    current_files = sorted(
        p for p in current_root.glob(f"*{file_suffix}") if p.is_file()
    )

    findings: list[StoryVarianceFinding] = []

    baseline_keys = {p.stem for p in baseline_files}
    current_keys = {p.stem for p in current_files}
    added = current_keys - baseline_keys
    removed = baseline_keys - current_keys
    common = baseline_keys & current_keys

    for name in sorted(added):
        current_path = current_root / f"{name}{file_suffix}"
        try:
            data = current_path.read_bytes()
        except OSError as exc:
            warnings.append(f"{name}: cannot read current PNG: {exc}")
            continue
        findings.append(
            StoryVarianceFinding(
                page_name=name,
                status="added",
                pixel_diff_pct=100.0,
                sha256_baseline="",
                sha256_current=_sha256_png(data),
                differing_lines=0,
                total_lines=0,
                note="page present in current but absent in baseline",
            )
        )
    for name in sorted(removed):
        findings.append(
            StoryVarianceFinding(
                page_name=name,
                status="removed",
                pixel_diff_pct=100.0,
                sha256_baseline="(absent)",
                sha256_current="(absent)",
                differing_lines=0,
                total_lines=0,
                note="page present in baseline but absent in current",
            )
        )

    for name in sorted(common):
        base_path = baseline_root / f"{name}{file_suffix}"
        cur_path = current_root / f"{name}{file_suffix}"
        try:
            base_data = base_path.read_bytes()
            cur_data = cur_path.read_bytes()
        except OSError as exc:
            warnings.append(f"{name}: {exc}")
            continue

        base_hash = _sha256_png(base_data)
        cur_hash = _sha256_png(cur_data)

        if base_hash == cur_hash:
            findings.append(
                StoryVarianceFinding(
                    page_name=name,
                    status="unchanged",
                    pixel_diff_pct=0.0,
                    sha256_baseline=base_hash,
                    sha256_current=cur_hash,
                    differing_lines=0,
                    total_lines=0,
                )
            )
            continue

        decoded_base = _decode_or_none(base_data, warnings, f"{name} baseline")
        decoded_cur = _decode_or_none(cur_data, warnings, f"{name} current")
        pixel_diff_pct = 0.0
        differing_lines = 0
        total_lines = 0
        if decoded_base is not None and decoded_cur is not None:
            if decoded_base[0] != decoded_cur[0] or decoded_base[1] != decoded_cur[1]:
                pixel_diff_pct = 100.0
                differing_lines = max(decoded_base[1], decoded_cur[1])
                total_lines = differing_lines
            else:
                width, height, _ = decoded_base
                differing, total = _per_pixel_diff(
                    width, height, decoded_base[2], decoded_cur[2]
                )
                pixel_diff_pct = round((differing / total) * 100.0, 3)
                # Per-row diff approximation: each differing pixel
                # belongs to one row.
                rows_with_diff = set()
                pos = 0
                for j in range(height):
                    for _i in range(width):
                        idx = pos * 3
                        if (
                            decoded_base[2][idx] != decoded_cur[2][idx]
                            or decoded_base[2][idx + 1] != decoded_cur[2][idx + 1]
                            or decoded_base[2][idx + 2] != decoded_cur[2][idx + 2]
                        ):
                            rows_with_diff.add(j)
                        pos += 1
                differing_lines = len(rows_with_diff)
                total_lines = height

        status = (
            "changed" if pixel_diff_pct > change_threshold_pct else "unchanged"
        )
        findings.append(
            StoryVarianceFinding(
                page_name=name,
                status=status,
                pixel_diff_pct=pixel_diff_pct,
                sha256_baseline=base_hash,
                sha256_current=cur_hash,
                differing_lines=differing_lines,
                total_lines=total_lines,
            )
        )

    pages_compared = len(findings)
    pages_unchanged = sum(1 for f in findings if f.status == "unchanged")
    pages_changed = sum(1 for f in findings if f.status == "changed")
    pages_added = sum(1 for f in findings if f.status == "added")
    pages_removed = sum(1 for f in findings if f.status == "removed")
    overall = (
        sum(f.pixel_diff_pct for f in findings) / pages_compared
        if pages_compared
        else 0.0
    )

    return StoryVarianceResult(
        findings=findings,
        pages_compared=pages_compared,
        pages_unchanged=pages_unchanged,
        pages_changed=pages_changed,
        pages_added=pages_added,
        pages_removed=pages_removed,
        overall_variance_pct=round(overall, 3),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Convenience: emit a StoryVariance JSONable dict for callers that don't
# care about the dataclass shape (e.g. logger / MCP wrapper).
# ---------------------------------------------------------------------------


def variance_to_dict(result: StoryVarianceResult) -> dict[str, Any]:
    return {
        "pages_compared": result.pages_compared,
        "pages_unchanged": result.pages_unchanged,
        "pages_changed": result.pages_changed,
        "pages_added": result.pages_added,
        "pages_removed": result.pages_removed,
        "overall_variance_pct": result.overall_variance_pct,
        "findings": [
            {
                "page_name": f.page_name,
                "status": f.status,
                "pixel_diff_pct": f.pixel_diff_pct,
                "sha256_baseline": f.sha256_baseline,
                "sha256_current": f.sha256_current,
                "differing_lines": f.differing_lines,
                "total_lines": f.total_lines,
                "note": f.note,
            }
            for f in result.findings
        ],
        "warnings": list(result.warnings),
    }


__all__ = [
    "StoryVarianceFinding",
    "StoryVarianceResult",
    "compute_story_variance",
    "variance_to_dict",
]
