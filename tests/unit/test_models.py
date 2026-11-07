"""Contract tests for assumptionradar.core.models (ADR-0004)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from assumptionradar.core.models import (
    Category,
    Confidence,
    Evidence,
    EvidenceKind,
    Finding,
    Severity,
    SourceLocation,
    VerificationStatus,
)

FP = "36fa9d46270fc5e3"


def make_finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "rule_id": "AR-CWD-001",
        "title": "Relative path depends on working directory",
        "message": "open('config.json') resolves against the current working directory",
        "category": Category.CWD,
        "severity": Severity.WARNING,
        "confidence": Confidence.HIGH,
        "fingerprint": FP,
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- enums


class TestEnums:
    def test_enum_values_are_lowercase_strings(self) -> None:
        assert Severity.WARNING.value == "warning"
        assert Confidence.HIGH.value == "high"
        assert EvidenceKind.REPRODUCED.value == "reproduced"
        assert VerificationStatus.NO_DIFFERENCE.value == "no_difference"
        assert Category.ENVIRONMENT.value == "environment"
        assert str(Severity.ERROR) == "error"
        assert isinstance(Category.CWD, str)

    def test_all_members(self) -> None:
        assert [s.value for s in Severity] == ["info", "warning", "error"]
        assert [c.value for c in Confidence] == ["low", "medium", "high"]
        assert [k.value for k in EvidenceKind] == ["observed", "inferred", "reproduced"]
        assert [v.value for v in VerificationStatus] == [
            "not_verified",
            "regression",
            "no_difference",
            "inconclusive",
            "unsupported",
            "error",
        ]
        assert {c.value for c in Category} == {
            "cwd",
            "environment",
            "filesystem",
            "timezone",
            "locale",
            "encoding",
            "network",
            "executables",
            "platform",
            "time",
            "imports",
            "test_order",
        }

    def test_severity_rank_orders(self) -> None:
        assert Severity.INFO.rank < Severity.WARNING.rank < Severity.ERROR.rank

    def test_severity_at_least(self) -> None:
        assert Severity.ERROR.at_least(Severity.WARNING)
        assert Severity.WARNING.at_least(Severity.WARNING)
        assert not Severity.INFO.at_least(Severity.WARNING)

    def test_confidence_rank_orders(self) -> None:
        assert Confidence.LOW.rank < Confidence.MEDIUM.rank < Confidence.HIGH.rank

    @pytest.mark.parametrize("text", ["warning", "WARNING", " Warning "])
    def test_parse_is_case_and_space_insensitive(self, text: str) -> None:
        assert Severity.parse(text) is Severity.WARNING

    def test_parse_error_lists_valid_values(self) -> None:
        with pytest.raises(ValueError, match=r"info, warning, error"):
            Severity.parse("fatal")

    def test_parse_works_on_every_enum(self) -> None:
        assert Confidence.parse("Medium") is Confidence.MEDIUM
        assert EvidenceKind.parse("OBSERVED") is EvidenceKind.OBSERVED
        assert VerificationStatus.parse("regression") is VerificationStatus.REGRESSION
        assert Category.parse("CWD") is Category.CWD


# ------------------------------------------------------------------ SourceLocation


class TestSourceLocation:
    def test_minimal(self) -> None:
        loc = SourceLocation("src/app.py")
        assert (loc.path, loc.line, loc.column, loc.symbol) == ("src/app.py", None, None, None)

    def test_full(self) -> None:
        loc = SourceLocation("src/app.py", line=10, column=5, symbol="load")
        assert loc.line == 10
        assert loc.column == 5
        assert loc.symbol == "load"

    @pytest.mark.parametrize(
        "bad_path",
        ["", "/etc/passwd", "C:/x/app.py", "C:\\x\\app.py", "src\\app.py", "../outside.py"],
    )
    def test_rejects_non_relative_posix_paths(self, bad_path: str) -> None:
        with pytest.raises(ValueError):
            SourceLocation(bad_path)

    @pytest.mark.parametrize(("line", "column"), [(0, None), (-1, None), (1, 0), (1, -3)])
    def test_line_and_column_are_one_based(self, line: int, column: int | None) -> None:
        with pytest.raises(ValueError):
            SourceLocation("a.py", line=line, column=column)

    def test_column_requires_line(self) -> None:
        with pytest.raises(ValueError):
            SourceLocation("a.py", column=3)

    def test_is_frozen(self) -> None:
        loc = SourceLocation("a.py", line=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            loc.line = 2  # type: ignore[misc]

    def test_hashable_and_equal(self) -> None:
        assert SourceLocation("a.py", 1) == SourceLocation("a.py", 1)
        assert len({SourceLocation("a.py", 1), SourceLocation("a.py", 1)}) == 1

    def test_from_path_makes_relative_posix(self, tmp_path: Path) -> None:
        file = tmp_path / "pkg" / "mod.py"
        loc = SourceLocation.from_path(file, tmp_path, line=3, symbol="f")
        assert loc == SourceLocation("pkg/mod.py", line=3, symbol="f")

    def test_from_path_accepts_strings(self, tmp_path: Path) -> None:
        loc = SourceLocation.from_path(str(tmp_path / "a.py"), str(tmp_path))
        assert loc.path == "a.py"

    def test_from_path_with_spaces_and_unicode(self, tmp_path: Path) -> None:
        file = tmp_path / "my dir" / "โมดูล.py"
        assert SourceLocation.from_path(file, tmp_path).path == "my dir/โมดูล.py"

    def test_from_path_outside_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside"):
            SourceLocation.from_path(tmp_path.parent / "x.py", tmp_path)

    def test_to_dict_has_fixed_keys(self) -> None:
        assert SourceLocation("a.py", 2, 4, "f").to_dict() == {
            "path": "a.py",
            "line": 2,
            "column": 4,
            "symbol": "f",
        }
        assert SourceLocation("a.py").to_dict() == {
            "path": "a.py",
            "line": None,
            "column": None,
            "symbol": None,
        }


# ------------------------------------------------------------------------ Evidence


class TestEvidence:
    def test_basic(self) -> None:
        ev = Evidence(EvidenceKind.OBSERVED, "audit-hook", "open('config.json', 'r')")
        assert ev.kind is EvidenceKind.OBSERVED
        assert ev.location is None

    def test_method_must_not_be_blank(self) -> None:
        with pytest.raises(ValueError):
            Evidence(EvidenceKind.OBSERVED, "  ", "x")

    def test_to_dict(self) -> None:
        ev = Evidence(EvidenceKind.INFERRED, "ast", "detail", SourceLocation("a.py", 1))
        assert ev.to_dict() == {
            "kind": "inferred",
            "method": "ast",
            "detail": "detail",
            "location": {"path": "a.py", "line": 1, "column": None, "symbol": None},
        }

    def test_to_dict_without_location(self) -> None:
        assert Evidence(EvidenceKind.REPRODUCED, "verification", "d").to_dict()["location"] is None


# ------------------------------------------------------------------------- Finding


class TestFinding:
    def test_defaults(self) -> None:
        f = make_finding()
        assert f.location is None
        assert f.evidence == ()
        assert f.verification is VerificationStatus.NOT_VERIFIED
        assert f.remediation == ""
        assert f.failure_scenario == ""
        assert f.docs_url is None
        assert f.test_id is None
        assert dict(f.metadata) == {}

    @pytest.mark.parametrize("rule_id", ["AR-CWD-001", "CUSTOM-001", "X1-Y2-Z3", "ACME-NET-12"])
    def test_valid_rule_ids(self, rule_id: str) -> None:
        assert make_finding(rule_id=rule_id).rule_id == rule_id

    @pytest.mark.parametrize(
        "rule_id", ["", "cwd", "ar-cwd-001", "AR", "AR_CWD_001", "AR--1", "-AR"]
    )
    def test_invalid_rule_ids(self, rule_id: str) -> None:
        with pytest.raises(ValueError, match="rule_id"):
            make_finding(rule_id=rule_id)

    @pytest.mark.parametrize("fp", [FP, FP + ":2", FP + ":10"])
    def test_valid_fingerprints(self, fp: str) -> None:
        assert make_finding(fingerprint=fp).fingerprint == fp

    @pytest.mark.parametrize("fp", ["", "xyz", FP.upper(), FP[:15], FP + "0", FP + ":", FP + ":a"])
    def test_invalid_fingerprints(self, fp: str) -> None:
        with pytest.raises(ValueError, match="fingerprint"):
            make_finding(fingerprint=fp)

    def test_title_and_message_required(self) -> None:
        with pytest.raises(ValueError):
            make_finding(title="")
        with pytest.raises(ValueError):
            make_finding(message="   ")

    def test_category_accepts_custom_string(self) -> None:
        f = make_finding(category="acme-database")
        assert f.category == "acme-database"

    def test_evidence_list_is_coerced_to_tuple(self) -> None:
        ev = Evidence(EvidenceKind.OBSERVED, "audit-hook", "d")
        f = make_finding(evidence=[ev])
        assert f.evidence == (ev,)
        assert isinstance(f.evidence, tuple)

    def test_metadata_is_read_only_copy(self) -> None:
        source = {"b": "2", "a": "1"}
        f = make_finding(metadata=source)
        source["c"] = "3"
        assert dict(f.metadata) == {"a": "1", "b": "2"}
        with pytest.raises(TypeError):
            f.metadata["x"] = "y"  # type: ignore[index]

    def test_metadata_values_must_be_strings(self) -> None:
        with pytest.raises(TypeError):
            make_finding(metadata={"a": 1})

    def test_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            make_finding().title = "x"  # type: ignore[misc]

    def test_hashable_even_with_metadata(self) -> None:
        f = make_finding(metadata={"a": "1"})
        assert hash(f) == hash(make_finding(metadata={"a": "1"}))

    def test_evidence_kinds(self) -> None:
        f = make_finding(
            evidence=[
                Evidence(EvidenceKind.OBSERVED, "audit-hook", "a"),
                Evidence(EvidenceKind.REPRODUCED, "verification", "b"),
                Evidence(EvidenceKind.OBSERVED, "audit-hook", "c"),
            ]
        )
        assert f.evidence_kinds == frozenset({EvidenceKind.OBSERVED, EvidenceKind.REPRODUCED})

    def test_sort_key(self) -> None:
        f = make_finding(location=SourceLocation("b.py", 3, 2))
        assert f.sort_key == ("b.py", 3, 2, "AR-CWD-001", FP)
        assert make_finding().sort_key == ("", 0, 0, "AR-CWD-001", FP)

    def test_sorting_is_by_location_then_rule(self) -> None:
        a = make_finding(rule_id="AR-ENV-001", location=SourceLocation("a.py", 5))
        b = make_finding(rule_id="AR-CWD-001", location=SourceLocation("a.py", 5))
        c = make_finding(location=SourceLocation("a.py", 2))
        d = make_finding(location=SourceLocation("B.py", 1))
        assert sorted([a, b, c, d], key=lambda f: f.sort_key) == [d, c, b, a]

    def test_with_verification_returns_new_finding(self) -> None:
        f = make_finding()
        ev = Evidence(EvidenceKind.REPRODUCED, "verification", "fails with cwd=<tmp>")
        g = f.with_verification(VerificationStatus.REGRESSION, ev)
        assert f.verification is VerificationStatus.NOT_VERIFIED
        assert f.evidence == ()
        assert g.verification is VerificationStatus.REGRESSION
        assert g.evidence == (ev,)
        assert g.fingerprint == f.fingerprint

    def test_with_verification_without_evidence(self) -> None:
        g = make_finding().with_verification(VerificationStatus.INCONCLUSIVE)
        assert g.verification is VerificationStatus.INCONCLUSIVE
        assert g.evidence == ()

    def test_to_dict_key_order_and_values(self) -> None:
        f = make_finding(
            location=SourceLocation("src/app/config.py", 12, 9, "load_settings"),
            evidence=[Evidence(EvidenceKind.OBSERVED, "audit-hook", "open('config.json')")],
            remediation="Resolve relative to Path(__file__).parent",
            failure_scenario="Fails when run from another directory",
            docs_url="https://example.invalid/rules/AR-CWD-001",
            test_id="tests/test_cfg.py::test_load",
            metadata={"z": "1", "a": "2"},
        )
        d = f.to_dict()
        assert list(d) == [
            "rule_id",
            "fingerprint",
            "title",
            "message",
            "category",
            "severity",
            "confidence",
            "verification",
            "location",
            "test_id",
            "evidence",
            "failure_scenario",
            "remediation",
            "docs_url",
            "metadata",
        ]
        assert d["severity"] == "warning"
        assert d["confidence"] == "high"
        assert d["verification"] == "not_verified"
        assert d["category"] == "cwd"
        assert d["evidence"] == [
            {
                "kind": "observed",
                "method": "audit-hook",
                "detail": "open('config.json')",
                "location": None,
            }
        ]
        assert list(d["metadata"]) == ["a", "z"]  # type: ignore[call-overload]

    def test_to_dict_is_json_serialisable_and_deterministic(self) -> None:
        f = make_finding(metadata={"b": "x", "a": "y"}, location=SourceLocation("a.py", 1))
        assert json.dumps(f.to_dict()) == json.dumps(
            make_finding(
                metadata={"a": "y", "b": "x"}, location=SourceLocation("a.py", 1)
            ).to_dict()
        )
