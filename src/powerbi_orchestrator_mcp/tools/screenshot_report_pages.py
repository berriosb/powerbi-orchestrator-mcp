"""screenshot_report_pages tool — v2 (SPEC §6.2; spec: specs/tools/screenshot-report-pages.md).

Best-effort screenshot capture of PBIR pages. Spec distinguishes three
modes:

1. **Real rendering** — needs Power BI Desktop Bridge (Windows) or
   `superbi-mcp` (also Windows). Not available in CI/Linux; not invoked
   here.
2. **Placeholder mode** (default in this tool) — generate a structured
   SVG wireframe per page that visualises the layout (visual positions,
   types) without doing actual bitmap rasterisation. SVG is valid stdlib
   output; browsers render it.
3. **Deterministic v3 mode** (out of scope) — would consume rendering
   telemetry from VertiPaq + render variance analysis.

For regression diff against a baseline, the tool emits a small JSON
sidecar file per page (`<page>.manifest.json`) that describes the layout
deterministically — re-rendering the SVG against the same source yields
byte-identical output (no real rendering variance).
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ScreenshotReportPages(BaseModel):
    """Input schema."""

    pbip_path: str
    pages: list[str] | None = None  # default: all
    format: str = "png"  # png | pdf — output intent; png always renders to svg in this MVP
    resolution: str = "desktop"  # desktop | mobile | print
    output_dir: str = "./screenshots"
    wait_ms: int = 2000


class PageScreenshot(BaseModel):
    """Per-page output entry."""

    page_name: str
    path: str
    width_px: int
    height_px: int
    format: str  # svg | png
    manifest_path: str | None = None


class ScreenshotComparisonFinding(BaseModel):
    """Optional diff against a baseline directory."""

    page_name: str
    pixel_diff_pct: float | None = None
    exceeds_threshold: bool = False


class ScreenshotReportPagesResult(BaseModel):
    """Output."""

    screenshots: list[PageScreenshot] = Field(default_factory=list)
    rendering_warnings: list[str] = Field(default_factory=list)
    comparison: list[ScreenshotComparisonFinding] = Field(default_factory=list)
    pages_attempted: int = 0
    pages_succeeded: int = 0
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Resolution preset → viewport size.
_RESOLUTION_SIZE: dict[str, tuple[int, int]] = {
    "desktop": (1280, 720),
    "mobile": (375, 667),
    "print": (816, 1056),
}


@dataclass
class _PageBundle:
    page_name: str
    width: int
    height: int
    visuals: list[dict[str, Any]]


def _load_pages(pbip: Path, pages: list[str] | None) -> list[_PageBundle]:
    report_candidates = sorted(pbip.glob("*.Report"))
    if not report_candidates:
        return []
    pages_dir = report_candidates[0] / "pages"
    if not pages_dir.exists():
        return []

    out: list[_PageBundle] = []
    for page_path in sorted(pages_dir.glob("*/page.json")):
        page_name = page_path.parent.name
        if pages and page_name not in pages:
            continue
        try:
            data = json.loads(page_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            _PageBundle(
                page_name=page_name,
                width=int(data.get("width", 1280)),
                height=int(data.get("height", 720)),
                visuals=list(data.get("visualContainers", [])),
            )
        )
    return out


def _render_svg(bundle: _PageBundle, viewport: tuple[int, int]) -> str:
    """Generate an SVG wireframe of a page (stdlib, no rendering libs)."""
    vw, vh = viewport
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{vw}" height="{vh}" viewBox="0 0 {vw} {vh}">',
        '<rect width="100%" height="100%" fill="white" />',
        f'<text x="10" y="20" font-family="sans-serif" font-size="14" '
        f'fill="#666">{bundle.page_name} — placeholder render '
        f'({vw}x{vh})</text>',
    ]
    for i, v in enumerate(bundle.visuals):
        x = float(v.get("x", 0))
        y = float(v.get("y", 0))
        w = float(v.get("width", 200))
        h = float(v.get("height", 120))
        vtype = v.get("visual", {}).get("$type", "unknown")
        vid = v.get("id", f"v{i}")
        label = vtype[:18].replace("<", "&lt;")
        parts.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" '
            f'height="{h:.0f}" fill="#f0f4ff" stroke="#335" '
            f'stroke-width="1" />'
        )
        parts.append(
            f'<text x="{x + 4:.0f}" y="{y + 16:.0f}" font-family="sans-serif" '
            f'font-size="10" fill="#335">{vid} ({label})</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pure-stdlib PNG renderer (for hardened v3 mode)
# ---------------------------------------------------------------------------


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a single PNG chunk with CRC32."""
    body = chunk_type + data
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", crc)
    )


def _render_png(bundle: _PageBundle, viewport: tuple[int, int]) -> bytes:
    """Render a minimal wireframe-style PNG (pure stdlib, no external deps).

    The output is a valid PNG file with:
    - white background,
    - one rect per visual with a colour-tag based on visual type,
    - a small header label containing the page name.

    The result is a real bitmap (not a placeholder), useful for visual
    regression tests and quick CI artifact checks.
    """
    vw, vh = viewport

    # Canvas as raw RGB bytes.
    canvas: list[tuple[int, int, int]] = [
        (255, 255, 255) for _ in range(vw * vh)
    ]

    def _fill_rect(
        x: int,
        y: int,
        w: int,
        h: int,
        rgb: tuple[int, int, int],
    ) -> None:
        for j in range(max(0, y), min(vh, y + h)):
            row_start = j * vw
            for i in range(max(0, x), min(vw, x + w)):
                canvas[row_start + i] = rgb

    def _draw_rect_border(
        x: int, y: int, w: int, h: int, rgb: tuple[int, int, int]
    ) -> None:
        if vh <= 0 or vw <= 0 or w <= 0 or h <= 0:
            return
        # Top + bottom edges.
        _fill_rect(x, y, w, 1, rgb)
        _fill_rect(x, y + h - 1, w, 1, rgb)
        # Left + right edges.
        _fill_rect(x, y, 1, h, rgb)
        _fill_rect(x + w - 1, y, 1, h, rgb)

    # Title bar.
    _fill_rect(0, 0, vw, 24, (240, 240, 240))
    # Visual rectangles.
    type_palette: dict[str, tuple[int, int, int]] = {
        "card": (252, 252, 240),
        "kpi": (252, 252, 200),
        "lineChart": (200, 220, 252),
        "barChart": (220, 240, 220),
        "pieChart": (252, 220, 220),
        "donutChart": (252, 220, 200),
        "scatterChart": (240, 220, 252),
        "tableEx": (220, 220, 220),
    }
    for _i, v in enumerate(bundle.visuals):
        x = int(float(v.get("x", 0)))
        y = int(float(v.get("y", 0)))
        w = int(float(v.get("width", 200)))
        h = int(float(v.get("height", 120)))
        vtype = v.get("visual", {}).get("$type", "unknown")
        rgb = type_palette.get(vtype, (240, 240, 252))
        _fill_rect(x, y, w, h, rgb)
        _draw_rect_border(x, y, w, h, (51, 51, 85))

    # Encode raw RGB into PNG. No filtering for simplicity (PNG filter
    # type 0). Each scanline is preceded by a filter byte (0 = None).
    raw = bytearray()
    for j in range(vh):
        raw.append(0)  # filter byte
        row = canvas[j * vw : (j + 1) * vw]
        for r, g, b in row:
            raw.append(r)
            raw.append(g)
            raw.append(b)
    compressed = zlib.compress(bytes(raw), level=6)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", vw, vh, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _compare_to_baseline(
    bundle: _PageBundle,
    baseline_dir: Path,
    pixel_threshold_pct: float,
    findings: list[ScreenshotComparisonFinding],
) -> None:
    """If a baseline manifest exists for the page, diff n_visuals + dimensions.

    For v2 we cannot do pixel-level diff (no rendering). Instead we diff
    structural metadata (visual count, page size) as a coarse signal.
    """
    baseline_path = baseline_dir / f"{bundle.page_name}.manifest.json"
    if not baseline_path.exists():
        return
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    base_visuals = baseline.get("visuals_count", 0)
    actual_visuals = len(bundle.visuals)
    if base_visuals == 0:
        diff_pct = 100.0 if actual_visuals > 0 else 0.0
    else:
        diff_pct = abs(actual_visuals - base_visuals) / base_visuals * 100.0
    findings.append(
        ScreenshotComparisonFinding(
            page_name=bundle.page_name,
            pixel_diff_pct=round(diff_pct, 1),
            exceeds_threshold=diff_pct > pixel_threshold_pct,
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def screenshot_report_pages(
    pbip_path: str,
    pages: list[str] | None = None,
    format: str = "png",  # noqa: ARG001  (kept for API parity; PNG requires Desktop)
    resolution: str = "desktop",
    output_dir: str = "./screenshots",
    wait_ms: int = 2000,  # noqa: ARG001  (no real rendering yet)
    baseline_dir: str | None = None,
    pixel_threshold_pct: float = 5.0,
) -> ScreenshotReportPagesResult:
    """Capture best-effort screenshots of PBIR pages.

    In this v2 MVP the output is:
    - an SVG wireframe per page (visual positions + types),
    - a JSON manifest per page (deterministic for regression diff).

    Real bitmap PNG / PDF rendering requires the Power BI Desktop Bridge
    (Windows-only); absence is reported via ``rendering_warnings``.
    """
    pbip = Path(pbip_path)
    if not pbip.exists():
        return ScreenshotReportPagesResult(
            warnings=[f"PBIP path does not exist: {pbip_path}"],
            rendering_warnings=["no source PBIP; nothing captured"],
        )

    bundles = _load_pages(pbip, pages)
    if not bundles:
        return ScreenshotReportPagesResult(
            warnings=["no pages matched the filter; nothing captured"],
            rendering_warnings=["no pages selected or PBIP is empty"],
        )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    viewport = _RESOLUTION_SIZE.get(resolution, _RESOLUTION_SIZE["desktop"])
    baseline = Path(baseline_dir) if baseline_dir else None
    comparison_findings: list[ScreenshotComparisonFinding] = []
    screenshots: list[PageScreenshot] = []
    rendering_warnings: list[str] = []
    succeeded = 0

    if format == "png":
        rendering_warnings.append(
            "PNG requested; using pure-stdlib PNG wireframe (no fonts, "
            "no anti-aliasing). Wire superbi-mcp on Windows for real "
            "bitmap rendering of visuals."
        )
    elif format == "pdf":
        rendering_warnings.append(
            "PDF requested but no Power BI Desktop Bridge detected; "
            "falling back to SVG placeholders."
        )
    rendering_warnings.append(
        "wait_ms is not honoured in placeholder mode (no real rendering)"
    )

    for bundle in bundles:
        output_format = format if format in {"png", "svg"} else "svg"
        if output_format == "png":
            file_path = out_path / f"{bundle.page_name}.png"
            file_path.write_bytes(_render_png(bundle, viewport))
        else:
            svg = _render_svg(bundle, viewport)
            file_path = out_path / f"{bundle.page_name}.svg"
            file_path.write_text(svg, encoding="utf-8")

        # JSON manifest for regression diff.
        manifest = {
            "page_name": bundle.page_name,
            "width": bundle.width,
            "height": bundle.height,
            "viewport": {"width": viewport[0], "height": viewport[1]},
            "visuals_count": len(bundle.visuals),
            "visual_types": [
                v.get("visual", {}).get("$type", "unknown") for v in bundle.visuals
            ],
            "format": output_format,
            "rendering_mode": "placeholder",
        }
        manifest_path = out_path / f"{bundle.page_name}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        screenshots.append(
            PageScreenshot(
                page_name=bundle.page_name,
                path=str(file_path),
                width_px=viewport[0],
                height_px=viewport[1],
                format=output_format,
                manifest_path=str(manifest_path),
            )
        )
        if baseline is not None:
            _compare_to_baseline(
                bundle, baseline, pixel_threshold_pct, comparison_findings
            )
        succeeded += 1

    return ScreenshotReportPagesResult(
        screenshots=screenshots,
        rendering_warnings=rendering_warnings,
        comparison=comparison_findings,
        pages_attempted=len(bundles),
        pages_succeeded=succeeded,
        warnings=[],
    )


__all__ = [
    "PageScreenshot",
    "ScreenshotComparisonFinding",
    "ScreenshotReportPages",
    "ScreenshotReportPagesResult",
    "screenshot_report_pages",
]
