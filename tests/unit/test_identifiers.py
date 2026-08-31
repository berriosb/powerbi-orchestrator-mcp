"""Tests for orchestrator.identifiers — canonical ID formats (spec §2.7)."""

from __future__ import annotations

import re
import string
from datetime import UTC

import pytest

from powerbi_orchestrator_mcp.orchestrator import identifiers as ids


class TestSessionId:
    def test_format_is_uuid_hex(self) -> None:
        sid = ids.new_session_id()
        assert ids.is_valid_session_id(sid)
        assert len(sid) == 32
        assert all(c in string.hexdigits.lower() for c in sid)

    def test_unique(self) -> None:
        a = ids.new_session_id()
        b = ids.new_session_id()
        assert a != b

    @pytest.mark.parametrize("bad", ["", "not-hex", "Z" * 32, "a" * 31, "a" * 33])
    def test_invalid_format_rejected(self, bad: str) -> None:
        assert not ids.is_valid_session_id(bad)


class TestPlanId:
    def test_format(self) -> None:
        pid = ids.new_plan_id()
        assert ids.is_valid_plan_id(pid)
        assert pid.startswith("plan_")

    def test_uses_provided_date(self) -> None:
        from datetime import datetime

        d = datetime(2026, 1, 15, tzinfo=UTC)
        pid = ids.new_plan_id(d)
        assert pid.startswith("plan_2026-01-15_")

    def test_unique_across_calls(self) -> None:
        seen = {ids.new_plan_id() for _ in range(50)}
        assert len(seen) == 50

    def test_parse(self) -> None:
        date, suffix = ids.parse_plan_id("plan_2026-08-21_abcd1234")
        assert date == "2026-08-21"
        assert suffix == "abcd1234"

    def test_parse_rejects_bad(self) -> None:
        with pytest.raises(ValueError):
            ids.parse_plan_id("not-a-plan-id")


class TestStepId:
    def test_derives_from_plan_id(self) -> None:
        plan_id = "plan_2026-08-21_abcdefgh"
        step = ids.new_step_id(plan_id, 3)
        assert step == "plan_2026-08-21_abcdefgh:s3"
        assert ids.is_valid_step_id(step)

    def test_invalid_plan_rejected(self) -> None:
        with pytest.raises(ValueError):
            ids.new_step_id("not-a-plan", 1)

    def test_negative_step_rejected(self) -> None:
        with pytest.raises(ValueError):
            ids.new_step_id("plan_2026-08-21_abcdefgh", -1)

    def test_parse(self) -> None:
        plan_id, n = ids.parse_step_id("plan_2026-08-21_abcdefgh:s7")
        assert plan_id == "plan_2026-08-21_abcdefgh"
        assert n == 7


class TestExecutionId:
    def test_format(self) -> None:
        eid = ids.new_execution_id()
        assert ids.is_valid_execution_id(eid)
        assert eid.startswith("exec_")
        assert len(eid) == len("exec_") + 16

    def test_unique(self) -> None:
        a = ids.new_execution_id()
        b = ids.new_execution_id()
        assert a != b


class TestTargetId:
    def test_short_safe_ref_kept_as_is(self) -> None:
        tid = ids.make_target_id("pbip_folder", "./out/sales.pbip")
        assert tid == "pbip_folder:./out/sales.pbip"

    def test_long_ref_is_hashed(self) -> None:
        long_ref = "/" + "a" * 250
        tid = ids.make_target_id("pbip_folder", long_ref)
        assert tid.startswith("pbip_folder:sha256:")
        assert ids.is_valid_target_id(tid)

    @pytest.mark.parametrize(
        "ref",
        [
            "/Users/jane.doe/work/sales.pbip",
            "/home/jane/work/sales.pbip",
            "C:\\Users\\jane.doe\\sales.pbip",
            "\\\\Users\\\\jane\\\\sales.pbip",
        ],
    )
    def test_pii_paths_are_hashed(self, ref: str) -> None:
        tid = ids.make_target_id("pbip_folder", ref)
        assert tid.startswith("pbip_folder:sha256:"), ref

    def test_empty_ref_rejected(self) -> None:
        with pytest.raises(ValueError):
            ids.make_target_id("pbip_folder", "")

    def test_empty_type_rejected(self) -> None:
        with pytest.raises(ValueError):
            ids.make_target_id("", "x")

    def test_supported_target_types(self) -> None:
        for t in ("pbip_folder", "pbix_file", "fabric_workspace", "pbi_desktop", "xmla_endpoint"):
            tid = ids.make_target_id(t, "ref")
            assert tid.startswith(f"{t}:")
            assert ids.is_valid_target_id(tid)


class TestRollbackHandle:
    def test_format(self) -> None:
        h = ids.new_rollback_handle()
        assert ids.is_valid_rollback_handle(h)
        assert h.startswith("snap_")
        assert re.match(ids.ROLLBACK_HANDLE_PATTERN, h)

    def test_unique(self) -> None:
        a = ids.new_rollback_handle()
        b = ids.new_rollback_handle()
        assert a != b


class TestSnapshotLabel:
    @pytest.mark.parametrize(
        "label",
        [
            "pre-rename-2026-08-21",
            "x",
            "a-b-c-d",
            "v1",
        ],
    )
    def test_valid_labels_accepted(self, label: str) -> None:
        assert ids.make_snapshot_label(label) == label

    @pytest.mark.parametrize(
        "label",
        [
            "",
            "Has Spaces",
            "Has_Underscore",
            "Has.UPPER",
            "x" * 200,  # exceeds 64 chars
        ],
    )
    def test_invalid_labels_rejected(self, label: str) -> None:
        with pytest.raises(ValueError):
            ids.make_snapshot_label(label)
