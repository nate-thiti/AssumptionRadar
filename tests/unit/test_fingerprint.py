"""Contract tests for assumptionradar.core.fingerprint (ADR-0004)."""

from __future__ import annotations

import re

from assumptionradar.core.fingerprint import compute_fingerprint, disambiguate, normalize_key
from assumptionradar.core.models import Category, Confidence, Finding, Severity, SourceLocation

HEX16 = re.compile(r"^[0-9a-f]{16}$")


def finding(fp: str, path: str | None = "a.py", line: int = 1, rule: str = "AR-CWD-001") -> Finding:
    return Finding(
        rule_id=rule,
        title="t",
        message="m",
        category=Category.CWD,
        severity=Severity.WARNING,
        confidence=Confidence.HIGH,
        fingerprint=fp,
        location=SourceLocation(path, line) if path else None,
    )


class TestComputeFingerprint:
    def test_golden_values_never_change(self) -> None:
        # These values are part of the public contract: baselines depend on them.
        assert (
            compute_fingerprint("AR-CWD-001", "src/app/config.py", "load_settings", "config.json")
            == "36fa9d46270fc5e3"
        )
        assert compute_fingerprint("AR-ENV-001", None, None, "DATABASE_URL") == "4a96f2ba0e170c27"

    def test_format(self) -> None:
        assert HEX16.match(compute_fingerprint("AR-X-1", "a.py", "f", "k"))

    def test_windows_separators_normalised(self) -> None:
        assert compute_fingerprint(
            "AR-CWD-001", "src\\app\\config.py", "load_settings", "config.json"
        ) == ("36fa9d46270fc5e3")

    def test_whitespace_in_key_normalised(self) -> None:
        a = compute_fingerprint("AR-X-1", "a.py", "f", "open(  'x' ,\n 'r')")
        b = compute_fingerprint("AR-X-1", "a.py", "f", "open( 'x' , 'r')")
        assert a == b

    def test_each_component_changes_result(self) -> None:
        base = compute_fingerprint("AR-X-1", "a.py", "f", "k")
        assert compute_fingerprint("AR-X-2", "a.py", "f", "k") != base
        assert compute_fingerprint("AR-X-1", "b.py", "f", "k") != base
        assert compute_fingerprint("AR-X-1", "a.py", "g", "k") != base
        assert compute_fingerprint("AR-X-1", "a.py", "f", "k2") != base

    def test_none_and_empty_are_equivalent(self) -> None:
        assert compute_fingerprint("AR-X-1", None, None, "k") == compute_fingerprint(
            "AR-X-1", "", "", "k"
        )

    def test_components_cannot_be_confused_by_concatenation(self) -> None:
        assert compute_fingerprint("AR-X-1", "ab", "c", "k") != compute_fingerprint(
            "AR-X-1", "a", "bc", "k"
        )

    def test_unicode(self) -> None:
        assert HEX16.match(compute_fingerprint("AR-X-1", "โมดูล.py", "ฟังก์ชัน", "ค่า"))


class TestNormalizeKey:
    def test_collapses_and_strips_whitespace(self) -> None:
        assert normalize_key("  a \t b\n\nc  ") == "a b c"

    def test_empty(self) -> None:
        assert normalize_key("   ") == ""


class TestDisambiguate:
    def test_returns_sorted_tuple(self) -> None:
        f1 = finding("1111111111111111", "b.py")
        f2 = finding("2222222222222222", "a.py")
        out = disambiguate([f1, f2])
        assert isinstance(out, tuple)
        assert [f.fingerprint for f in out] == ["2222222222222222", "1111111111111111"]

    def test_duplicates_get_numbered_suffix_in_sorted_order(self) -> None:
        fp = "abcdefabcdefabcd"
        later = finding(fp, "a.py", line=20)
        first = finding(fp, "a.py", line=3)
        middle = finding(fp, "a.py", line=10)
        out = disambiguate([later, first, middle])
        assert [(f.location.line, f.fingerprint) for f in out if f.location] == [
            (3, fp),
            (10, fp + ":2"),
            (20, fp + ":3"),
        ]

    def test_input_order_does_not_matter(self) -> None:
        fp = "abcdefabcdefabcd"
        items = [
            finding(fp, "a.py", 1),
            finding(fp, "a.py", 2),
            finding("0000000000000000", "z.py"),
        ]
        assert disambiguate(items) == disambiguate(list(reversed(items)))

    def test_already_suffixed_fingerprints_are_reset(self) -> None:
        fp = "abcdefabcdefabcd"
        out = disambiguate([finding(fp + ":7", "a.py", 1), finding(fp, "a.py", 2)])
        assert [f.fingerprint for f in out] == [fp, fp + ":2"]

    def test_empty(self) -> None:
        assert disambiguate([]) == ()
