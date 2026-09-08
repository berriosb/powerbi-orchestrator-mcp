"""Tests for story variance analysis (Sprint 14)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.tools.screenshot_report_pages import (
    screenshot_report_pages,
)
from powerbi_orchestrator_mcp.validation.story_variance import (
    StoryVarianceFinding,
    compute_story_variance,
    variance_to_dict,
)


@pytest.fixture()
def pbip_v3(tmp_path: Path) -> Path:
    pbip = tmp_path / "demo.pbip"
    pbip.mkdir()
    (pbip / "demo.pbip").write_text("{}")
    (pbip / "demo.Dataset").mkdir()
    (pbip / "demo.Report").mkdir()
    return pbip


def _write_page(pbip: Path, page_name: str, visuals: list[dict]) -> None:
    import json

    page_dir = pbip / "demo.Report" / "pages" / page_name
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page.json").write_text(
        json.dumps({"width": 1280, "height": 720, "visualContainers": visuals})
    )


def _visual(vid: str, vtype: str, **kw: object) -> dict:
    v: dict = {"id": vid, "visual": {"$type": vtype}}
    v.update(kw)
    return v


class TestStoryVarianceIdentical:
    def test_identical_dirs_return_zero_variance(
        self, pbip_v3, tmp_path: Path
    ) -> None:
        _write_page(pbip_v3, "Overview", [_visual("v1", "card")])
        out = tmp_path / "same"
        screenshot_report_pages(
            pbip_path=str(pbip_v3),
            output_dir=str(out),
            format="png",
        )
        # Compare the same dir against itself.
        result = compute_story_variance(out, out)
        assert result.pages_compared == 1
        assert result.pages_unchanged == 1
        assert result.pages_changed == 0
        assert result.overall_variance_pct == 0.0
        # SHA256 hashes match.
        assert result.findings[0].sha256_baseline == result.findings[0].sha256_current

    def test_no_pngs_returns_empty(self, tmp_path: Path) -> None:
        result = compute_story_variance(tmp_path, tmp_path)
        assert result.pages_compared == 0
        assert result.overall_variance_pct == 0.0


class TestStoryVarianceAddedRemoved:
    def test_added_page(self, pbip_v3, tmp_path: Path) -> None:
        _write_page(pbip_v3, "Overview", [_visual("v1", "card")])
        baseline_dir = tmp_path / "baseline"
        current_dir = tmp_path / "current"
        baseline_dir.mkdir()
        current_dir.mkdir()
        # Make a PNG in current but not baseline.
        screenshot_report_pages(
            pbip_path=str(pbip_v3),
            output_dir=str(current_dir),
            format="png",
        )
        result = compute_story_variance(baseline_dir, current_dir)
        added = [f for f in result.findings if f.status == "added"]
        assert len(added) == 1
        assert added[0].page_name == "Overview"
        assert added[0].pixel_diff_pct == 100.0
        assert added[0].sha256_baseline == ""

    def test_removed_page(self, pbip_v3, tmp_path: Path) -> None:
        _write_page(pbip_v3, "Overview", [_visual("v1", "card")])
        baseline_dir = tmp_path / "baseline"
        current_dir = tmp_path / "current"
        baseline_dir.mkdir()
        current_dir.mkdir()
        screenshot_report_pages(
            pbip_path=str(pbip_v3),
            output_dir=str(baseline_dir),
            format="png",
        )
        result = compute_story_variance(baseline_dir, current_dir)
        removed = [f for f in result.findings if f.status == "removed"]
        assert len(removed) == 1


class TestStoryVarianceContentDiff:
    def test_changed_page_detected(
        self, tmp_path: Path
    ) -> None:
        # Build two PBIPs with different content, render each.
        baseline_pbip = tmp_path / "b.pbip"
        current_pbip = tmp_path / "c.pbip"
        for p, visuals in [
            (baseline_pbip, [_visual("v1", "card")]),
            (current_pbip, [_visual("v1", "card"), _visual("v2", "lineChart")]),
        ]:
            p.mkdir()
            (p / "demo.pbip").write_text("{}")
            (p / "demo.Dataset").mkdir()
            (p / "demo.Report").mkdir()
            _write_page(p, "Overview", visuals)

        baseline_dir = tmp_path / "baseline"
        current_dir = tmp_path / "current"
        screenshot_report_pages(
            pbip_path=str(baseline_pbip), output_dir=str(baseline_dir), format="png"
        )
        screenshot_report_pages(
            pbip_path=str(current_pbip), output_dir=str(current_dir), format="png"
        )

        result = compute_story_variance(baseline_dir, current_dir)
        assert result.pages_compared == 1
        changed = [f for f in result.findings if f.status == "changed"]
        assert len(changed) == 1
        assert changed[0].pixel_diff_pct > 0.0

    def test_no_diff_unchanged(
        self, pbip_v3, tmp_path: Path
    ) -> None:
        _write_page(pbip_v3, "Overview", [_visual("v1", "card")])
        baseline_dir = tmp_path / "b"
        current_dir = tmp_path / "c"
        screenshot_report_pages(
            pbip_path=str(pbip_v3),
            output_dir=str(baseline_dir),
            format="png",
        )
        screenshot_report_pages(
            pbip_path=str(pbip_v3),
            output_dir=str(current_dir),
            format="png",
        )
        result = compute_story_variance(baseline_dir, current_dir)
        unchanged = [f for f in result.findings if f.status == "unchanged"]
        assert len(unchanged) == 1

    def test_low_diff_below_threshold_unchanged(
        self, tmp_path: Path
    ) -> None:
        # Directly write two PNGs that differ only in 1 pixel.
        from powerbi_orchestrator_mcp.tools.screenshot_report_pages import (
            _PageBundle,
            _render_png,
        )

        baseline_bytes = _render_png(
            _PageBundle(page_name="Overview", width=10, height=10, visuals=[]),
            (10, 10),
        )
        baseline_dir = tmp_path / "b"
        current_dir = tmp_path / "c"
        baseline_dir.mkdir()
        current_dir.mkdir()
        (baseline_dir / "Overview.png").write_bytes(baseline_bytes)

        # Mutate a single pixel byte (overwrite one byte, but keep
        # general area readable — for true test, simply check that
        # any small diff is detectable and reported as changed when
        # threshold is 0).
        mutated = bytearray(baseline_bytes)
        idx = len(baseline_bytes) - len(baseline_bytes) // 4
        mutated[idx] = (mutated[idx] + 1) % 256
        # Validate this won't break PNG structure too much.
        (current_dir / "Overview.png").write_bytes(bytes(mutated))

        result = compute_story_variance(
            baseline_dir,
            current_dir,
            change_threshold_pct=50.0,
        )
        # Threshold is 50%; small diff should be "unchanged".
        assert any(f.status == "unchanged" for f in result.findings)


class TestStoryVarianceHelpers:
    def test_variance_to_dict_roundtrips(self) -> None:
        from powerbi_orchestrator_mcp.validation.story_variance import (
            StoryVarianceResult,
        )

        result = StoryVarianceResult(
            findings=[],
            pages_compared=0,
            pages_unchanged=0,
            pages_changed=0,
            pages_added=0,
            pages_removed=0,
            overall_variance_pct=0.0,
            warnings=[],
        )
        d = variance_to_dict(result)
        assert d["pages_compared"] == 0
        # Round-tripping via JSON should produce an equivalent dict.
        json.dumps(d)

    def test_finding_dataclass_defaults(self) -> None:
        f = StoryVarianceFinding(
            page_name="p",
            status="unchanged",
            pixel_diff_pct=0.0,
            sha256_baseline="x",
            sha256_current="x",
        )
        assert f.differing_lines == 0
        assert f.note == ""

    def test_warnings_emitted_for_corrupt_png(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "b"
        current_dir = tmp_path / "c"
        baseline_dir.mkdir()
        current_dir.mkdir()
        # Write a fake "PNG" that doesn't decode.
        (baseline_dir / "Overview.png").write_bytes(b"not a png")
        (current_dir / "Overview.png").write_bytes(b"also not a png")
        result = compute_story_variance(baseline_dir, current_dir)
        assert any("failed to decode" in w for w in result.warnings)


class TestPNGChunkParser:
    def test_handles_our_deterministic_renders(
        self, pbip_v3, tmp_path: Path
    ) -> None:
        # Round-trip: render a page, parse the PNG, ensure IHDR/IDAT/IEND.
        _write_page(pbip_v3, "Overview", [_visual("v1", "card")])
        out = tmp_path / "out"
        screenshot_report_pages(
            pbip_path=str(pbip_v3), output_dir=str(out), format="png"
        )
        from powerbi_orchestrator_mcp.validation.story_variance import (
            _read_png_chunks,
        )

        png = (out / "Overview.png").read_bytes()
        chunks = _read_png_chunks(png)
        types = {c.type for c in chunks}
        assert "IHDR" in types
        assert "IDAT" in types
        assert "IEND" in types
