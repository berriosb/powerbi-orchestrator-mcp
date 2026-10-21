"""Tests for the abbreviate_names tool.

The tool proposes shorter names. What matters is that a proposal is
safe: it does not truncate a name into nonsense, and it does not
collide with a name that already exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from powerbi_orchestrator_mcp.tools.abbreviate_names import abbreviate_names


def _project(
    tmp_path: Path,
    measures: dict[str, str] | None = None,
    columns: list[str] | None = None,
) -> Path:
    root = tmp_path / "demo.pbip"
    root.mkdir()
    (root / "demo.pbip").write_text("{}", encoding="utf-8")
    report = root / "demo.Report"
    report.mkdir()
    (report / "report.json").write_text("{}", encoding="utf-8")
    dataset = root / "demo.Dataset"
    dataset.mkdir()
    (dataset / "definition.pbism").write_text(
        json.dumps(
            {
                "model": {
                    "tables": [
                        {
                            "name": "FactSales",
                            "columns": [
                                {"name": name, "dataType": "string"} for name in (columns or [])
                            ],
                            "measures": [
                                {"name": name, "expression": expression}
                                for name, expression in (measures or {}).items()
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return root


class TestProposals:
    def test_a_long_name_gets_a_proposal(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={
                "Total Revenue Amount In Local Currency Including Tax": "SUM([A])",
            },
        )

        result = abbreviate_names(str(root), kind="measures", max_length=30)

        assert result["success"] is True
        assert result["names_with_a_proposal"] == 1
        proposal = result["proposals"][0]
        assert proposal["proposed"] == "Tot Rev Amt Loc Cur Incl Tax"

    def test_a_short_name_is_left_alone(self, tmp_path: Path) -> None:
        root = _project(tmp_path, measures={"Total": "SUM([A])"})

        result = abbreviate_names(str(root), kind="measures")

        proposal = result["proposals"][0]
        assert proposal["proposed"] is None
        assert "within the limit" in proposal["reason"]

    def test_it_says_what_it_changed(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={"Total Revenue Amount In Local Currency": "SUM([A])"},
        )

        result = abbreviate_names(str(root), kind="measures", max_length=30)
        proposal = result["proposals"][0]

        rules = {change["rule"] for change in proposal["changes"]}
        assert "standard abbreviation" in rules
        assert "dropped a filler word" in rules

    def test_the_rename_map_is_ready_for_the_planner(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={"Total Revenue Amount In Local Currency": "SUM([A])"},
        )

        result = abbreviate_names(str(root), kind="measures", max_length=30)

        assert result["rename_map"] == {
            "Total Revenue Amount In Local Currency": "Tot Rev Amt Loc Cur"
        }

    def test_the_filler_words_can_be_kept(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={"Total Revenue Amount In Local Currency": "SUM([A])"},
        )

        result = abbreviate_names(
            str(root), kind="measures", max_length=30, drop_filler_words=False
        )

        assert result["rename_map"] == {
            "Total Revenue Amount In Local Currency": "Tot Rev Amt In Loc Cur"
        }

    def test_the_abbreviations_can_be_turned_off(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={"Total Revenue Amount In Local Currency": "SUM([A])"},
        )

        # The name is 38 characters and the shortened one is 35, so the
        # limit has to sit between the two for there to be a proposal.
        result = abbreviate_names(str(root), kind="measures", max_length=36, abbreviate_words=False)

        assert result["rename_map"] == {
            "Total Revenue Amount In Local Currency": "Total Revenue Amount Local Currency"
        }


class TestWhatItRefusesToDo:
    def test_a_name_it_cannot_shorten_is_reported(self, tmp_path: Path) -> None:
        """Truncating a name would say less than the original."""
        root = _project(
            tmp_path,
            measures={
                "Revenue Recognition Performance Measurement Indicator Metric": "SUM([A])",
            },
        )

        result = abbreviate_names(str(root), kind="measures")

        assert result["names_needing_a_name_by_hand"] == 1
        proposal = result["proposals"][0]
        assert proposal["proposed"] is None
        assert proposal["needs_a_name_by_hand"] is True
        assert "without changing what it means" in proposal["reason"]

    def test_a_proposal_does_not_collide_with_an_existing_name(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={
                "Tot Rev Amt Loc Cur": "SUM([A])",
                "Total Revenue Amount In Local Currency": "SUM([B])",
            },
        )

        result = abbreviate_names(str(root), kind="measures", max_length=30)

        proposed = result["proposals"][1]["proposed"]
        assert proposed != "Tot Rev Amt Loc Cur"
        assert result["proposals"][1]["collision_resolved"] is True

    def test_two_proposals_do_not_collide_with_each_other(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={
                "Total Revenue Amount In Local Currency": "SUM([A])",
                "Total Revenue Amount In Local Currency Two": "SUM([B])",
            },
        )

        result = abbreviate_names(str(root), kind="measures", max_length=30)

        proposals = [p["proposed"] for p in result["proposals"] if p["proposed"]]
        assert len(proposals) == len(set(proposals))


class TestColumns:
    def test_the_columns_are_considered(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            columns=["Total Revenue Amount In Local Currency Including Tax"],
        )

        result = abbreviate_names(str(root), kind="columns")

        assert result["names_with_a_proposal"] == 1
        assert result["proposals"][0]["kind"] == "column"
        assert result["proposals"][0]["table"] == "FactSales"

    def test_both_kinds_are_considered_by_default(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={"Total Revenue Amount In Local Currency": "SUM([A])"},
            columns=["Total Revenue Amount In Local Currency Two"],
        )

        result = abbreviate_names(str(root))

        kinds = {p["kind"] for p in result["proposals"]}
        assert kinds == {"measure", "column"}


class TestTheArguments:
    def test_an_unknown_kind_is_refused(self, tmp_path: Path) -> None:
        root = _project(tmp_path, measures={"A": "SUM([A])"})

        result = abbreviate_names(str(root), kind="nope")

        assert result["success"] is False
        assert "must be" in (result["error_message"] or "")

    def test_a_maximum_length_below_eight_is_refused(self, tmp_path: Path) -> None:
        root = _project(tmp_path, measures={"A": "SUM([A])"})

        result = abbreviate_names(str(root), max_length=3)

        assert result["success"] is False
        assert "at least 8" in (result["error_message"] or "")

    def test_a_project_that_cannot_be_read_is_reported(self, tmp_path: Path) -> None:
        result = abbreviate_names(str(tmp_path / "nope"))

        assert result["success"] is False
        assert "does not exist" in (result["error_message"] or "")


class TestItWritesNothing:
    def test_the_project_is_untouched(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            measures={"Total Revenue Amount In Local Currency": "SUM([A])"},
        )
        before = (root / "demo.Dataset" / "definition.pbism").read_bytes()

        abbreviate_names(str(root))

        assert (root / "demo.Dataset" / "definition.pbism").read_bytes() == before
