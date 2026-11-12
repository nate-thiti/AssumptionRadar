"""Tests for baseline files and comparison (P2-9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import assumptionradar
from assumptionradar.baseline import (
    BASELINE_KIND,
    BASELINE_SCHEMA_VERSION,
    Baseline,
    BaselineEntry,
    compare,
    load_baseline,
    write_baseline,
)
from assumptionradar.config import Settings, scan_with_settings
from assumptionradar.core.exceptions import AssumptionRadarError, BaselineError
from assumptionradar.core.models import Finding, Severity
from assumptionradar.static.engine import scan
from assumptionradar.static.rules import RelativePathAccessRule, UndeclaredEnvVarRule


def findings_for(root: Path, source: str) -> tuple[Finding, ...]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "m.py").write_text(source, encoding="utf-8")
    rules = (RelativePathAccessRule(), UndeclaredEnvVarRule(()))
    return scan(root, rules).findings


TWO = "open('a.json')\nopen('b.json')\n"


# ------------------------------------------------------------------- the file


def test_constants() -> None:
    assert BASELINE_SCHEMA_VERSION == "1"
    assert BASELINE_KIND == "assumptionradar-baseline"


def test_baseline_document_shape(tmp_path: Path) -> None:
    findings = findings_for(tmp_path, TWO)
    baseline = Baseline.from_findings(findings)
    data = baseline.to_dict()
    assert list(data) == ["schema_version", "kind", "tool", "findings"]
    assert data["schema_version"] == "1"
    assert data["kind"] == "assumptionradar-baseline"
    assert data["tool"] == {"name": "assumptionradar", "version": assumptionradar.__version__}
    entries = data["findings"]
    assert isinstance(entries, list)
    assert [e["fingerprint"] for e in entries] == sorted(f.fingerprint for f in findings)
    first = entries[0]
    assert list(first) == ["fingerprint", "rule_id", "path", "symbol", "message"]
    assert first["rule_id"] == "AR-CWD-001"
    assert first["path"] == "m.py"
    assert first["symbol"] is None


def test_entry_from_finding(tmp_path: Path) -> None:
    (finding,) = findings_for(tmp_path, "def load():\n    open('a.json')\n")
    entry = BaselineEntry.from_finding(finding)
    assert entry == BaselineEntry(
        fingerprint=finding.fingerprint,
        rule_id="AR-CWD-001",
        path="m.py",
        symbol="load",
        message=finding.message,
    )
    assert BaselineEntry.from_dict(entry.to_dict()) == entry


def test_entries_are_sorted_by_fingerprint(tmp_path: Path) -> None:
    findings = findings_for(tmp_path, "open('z')\nopen('y')\nopen('x')\n")
    baseline = Baseline.from_findings(reversed(findings))
    fingerprints = [entry.fingerprint for entry in baseline.entries]
    assert fingerprints == sorted(fingerprints)
    assert baseline.fingerprints == frozenset(fingerprints)


def test_line_shifts_do_not_change_the_baseline(tmp_path: Path) -> None:
    before = Baseline.from_findings(findings_for(tmp_path / "a", TWO))
    after = Baseline.from_findings(findings_for(tmp_path / "b", "\n\n# moved\n" + TWO))
    assert before == after


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    baseline = Baseline.from_findings(findings_for(tmp_path / "p", TWO))
    path = tmp_path / "baseline.json"
    write_baseline(path, baseline)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == baseline.to_dict()
    assert load_baseline(path) == baseline

    first = path.read_bytes()
    write_baseline(path, load_baseline(path))
    assert path.read_bytes() == first  # deterministic: no timestamps


def test_empty_baseline(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    write_baseline(path, Baseline.from_findings(()))
    assert load_baseline(path).entries == ()


# ---------------------------------------------------------------- load errors


def baseline_error(path: Path) -> str:
    with pytest.raises(BaselineError) as info:
        load_baseline(path)
    return str(info.value)


def test_baseline_error_is_a_library_error() -> None:
    assert issubclass(BaselineError, AssumptionRadarError)


def test_missing_baseline(tmp_path: Path) -> None:
    path = tmp_path / "nope.json"
    assert baseline_error(path) == (
        f"{path}: baseline not found; run 'assumptionradar baseline create' first"
    )


VALID_ENTRY = {
    "fingerprint": "0123456789abcdef",
    "rule_id": "AR-CWD-001",
    "path": "m.py",
    "symbol": None,
    "message": "m",
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{not json", "is not valid JSON"),
        ("[]", "not an AssumptionRadar baseline"),
        ('{"schema_version": "1", "kind": "report", "findings": []}', "not an AssumptionRadar"),
        (
            '{"schema_version": "2", "kind": "assumptionradar-baseline", "findings": []}',
            "unsupported baseline schema_version '2'; this version reads '1'",
        ),
        (
            '{"schema_version": "1", "kind": "assumptionradar-baseline", "findings": {}}',
            "'findings' must be a list",
        ),
        (
            '{"schema_version": "1", "kind": "assumptionradar-baseline", "findings": [{}]}',
            "invalid baseline entry 0",
        ),
        (
            '{"schema_version": "1", "kind": "assumptionradar-baseline", "findings": ['
            '{"fingerprint": "XYZ", "rule_id": "AR-CWD-001", "path": "m.py", '
            '"symbol": null, "message": "m"}]}',
            "invalid baseline entry 0",
        ),
        (
            json.dumps(
                {
                    "schema_version": "1",
                    "kind": "assumptionradar-baseline",
                    "findings": [VALID_ENTRY, VALID_ENTRY],
                }
            ),
            "duplicate fingerprint '0123456789abcdef'",
        ),
    ],
)
def test_invalid_baselines(tmp_path: Path, text: str, expected: str) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(text, encoding="utf-8")
    message = baseline_error(path)
    assert message.startswith(f"{path}: ")
    assert expected in message


def test_tool_block_is_optional_when_loading(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {"schema_version": "1", "kind": "assumptionradar-baseline", "findings": [VALID_ENTRY]}
        ),
        encoding="utf-8",
    )
    assert load_baseline(path).entries == (BaselineEntry.from_dict(VALID_ENTRY),)


# ----------------------------------------------------------------- comparison


def test_compare_splits_new_unchanged_resolved(tmp_path: Path) -> None:
    old = findings_for(tmp_path / "old", TWO)
    baseline = Baseline.from_findings(old)
    current = findings_for(tmp_path / "new", "open('a.json')\nopen('c.json')\n")
    result = compare(baseline, current)

    a_print = next(f.fingerprint for f in old if "a.json" in f.message)
    b_print = next(f.fingerprint for f in old if "b.json" in f.message)
    assert [f.fingerprint for f in result.unchanged] == [a_print]
    assert [f.message for f in result.new] == [
        next(f.message for f in current if "c.json" in f.message)
    ]
    assert [e.fingerprint for e in result.resolved] == [b_print]


def test_compare_keeps_finding_order_and_sorts_resolved(tmp_path: Path) -> None:
    baseline = Baseline.from_findings(findings_for(tmp_path / "old", "open('q')\nopen('r')\n"))
    current = findings_for(tmp_path / "new", "open('x')\nopen('y')\nopen('z')\n")
    result = compare(baseline, current)
    assert result.new == current
    assert result.unchanged == ()
    resolved = [e.fingerprint for e in result.resolved]
    assert resolved == sorted(resolved)
    assert len(resolved) == 2


def test_compare_with_identical_findings(tmp_path: Path) -> None:
    findings = findings_for(tmp_path, TWO)
    result = compare(Baseline.from_findings(findings), findings)
    assert (result.new, result.resolved) == ((), ())
    assert result.unchanged == findings


def test_failing_respects_the_threshold(tmp_path: Path) -> None:
    current = findings_for(tmp_path, "import os\nopen('a.json')\nos.environ.get('TOKEN')\n")
    assert sorted(str(f.severity) for f in current) == ["info", "warning"]
    result = compare(Baseline.from_findings(()), current)
    assert {f.rule_id for f in result.failing(Severity.INFO)} == {"AR-CWD-001", "AR-ENV-002"}
    assert [f.rule_id for f in result.failing(Severity.WARNING)] == ["AR-CWD-001"]
    assert result.failing(Severity.ERROR) == ()


def test_unchanged_findings_never_fail(tmp_path: Path) -> None:
    findings = findings_for(tmp_path, TWO)
    result = compare(Baseline.from_findings(findings), findings)
    assert result.failing(Severity.INFO) == ()


def test_comparison_to_dict(tmp_path: Path) -> None:
    baseline = Baseline.from_findings(findings_for(tmp_path / "old", TWO))
    current = findings_for(tmp_path / "new", "open('a.json')\nopen('c.json')\n")
    result = compare(baseline, current)
    data = result.to_dict()
    assert list(data) == ["summary", "new", "unchanged", "resolved"]
    assert data["summary"] == {"new": 1, "unchanged": 1, "resolved": 1}
    assert data["new"] == [f.to_dict() for f in result.new]
    assert data["unchanged"] == [f.fingerprint for f in result.unchanged]
    assert data["resolved"] == [e.to_dict() for e in result.resolved]


# ------------------------------------------------------- scanning with settings


def test_scan_with_settings_applies_rules_and_excludes(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("open('a.json')\n", encoding="utf-8")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "x.py").write_text("open('b.json')\n", encoding="utf-8")
    settings = Settings(exclude=("skip/**",), cache=False)
    result = scan_with_settings(tmp_path, settings)
    assert [f.location.path for f in result.findings if f.location] == ["keep.py"]

    only_env = Settings(rules=("AR-ENV-001",), cache=False)
    assert scan_with_settings(tmp_path, only_env).findings == ()


def test_scan_with_settings_cache(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("open('a.json')\n", encoding="utf-8")
    scan_with_settings(tmp_path, Settings(cache=False))
    assert not (tmp_path / ".assumptionradar_cache").exists()

    settings = Settings(cache_dir="cachedir")
    first = scan_with_settings(tmp_path, settings)
    second = scan_with_settings(tmp_path, settings)
    assert (tmp_path / "cachedir").is_dir()
    assert (first.cache_misses, second.cache_hits) == (1, 1)
    assert first.findings == second.findings
