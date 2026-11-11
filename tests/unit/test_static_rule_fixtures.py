"""Exact-match tests for static rules against fixture files (P2-2 onwards).

Each ``tests/fixtures/static/<group>/*.py`` file is scanned with all default
rules. A line marked ``# expect: AR-X-001, AR-X-001`` must produce exactly those
findings (repeats count); every other line must produce none.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from assumptionradar.static.engine import scan
from assumptionradar.static.rules import default_rules

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "static"
_MARKER_RE = re.compile(r"#\s*expect:\s*(.+?)\s*$")

FIXTURE_FILES = sorted(FIXTURES.rglob("*.py"))


def expected_findings(path: Path) -> Counter[tuple[int, str]]:
    expected: Counter[tuple[int, str]] = Counter()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = _MARKER_RE.search(line)
        if match:
            for rule_id in match.group(1).split(","):
                expected[(number, rule_id.strip())] += 1
    return expected


def test_fixture_files_exist() -> None:
    names = {p.relative_to(FIXTURES).as_posix() for p in FIXTURE_FILES}
    assert {"cwd/positive.py", "cwd/negative.py"} <= names


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.relative_to(FIXTURES).as_posix())
def test_fixture_matches_markers(path: Path) -> None:
    result = scan(path.parent, default_rules(), include=(path.name,))
    assert result.errors == ()
    assert all(f.location is not None for f in result.findings)
    actual: Counter[tuple[int, str]] = Counter(
        (f.location.line or 0, f.rule_id) for f in result.findings if f.location is not None
    )
    assert actual == expected_findings(path)
    if path.stem == "negative":
        assert result.findings == ()
    if path.stem == "positive":
        assert result.findings
