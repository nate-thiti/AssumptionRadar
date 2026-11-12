"""Exact-match tests for static rules against fixture files (P2-2 onwards).

Each ``tests/fixtures/static/<group>/*.py`` file is scanned with all default
rules. Only findings of the group's own rules count: group ``cwd`` checks
``AR-CWD-*``, ``plat`` checks ``AR-PLAT-*`` and so on, so one fixture can use
code that another rule also reports. A line marked
``# expect: AR-X-001, AR-X-001`` must produce exactly those findings (repeats
count); every other line must produce none of the group's findings. A group
with a ``pyproject.toml`` is scanned with the project-aware rules.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from assumptionradar.core.models import Finding
from assumptionradar.static.engine import scan
from assumptionradar.static.project import load_project
from assumptionradar.static.rules import default_rules

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "static"
_MARKER_RE = re.compile(r"#\s*expect:\s*(.+?)\s*$")

FIXTURE_FILES = sorted(FIXTURES.glob("*/*.py"))


def group_prefix(path: Path) -> str:
    return f"AR-{path.parent.name.upper()}-"


def expected_findings(path: Path) -> Counter[tuple[int, str]]:
    expected: Counter[tuple[int, str]] = Counter()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = _MARKER_RE.search(line)
        if match:
            for rule_id in match.group(1).split(","):
                expected[(number, rule_id.strip())] += 1
    return expected


def group_findings(path: Path) -> list[Finding]:
    project = load_project(path.parent) if (path.parent / "pyproject.toml").exists() else None
    result = scan(path.parent, default_rules(project), include=(path.name,))
    assert result.errors == ()
    return [f for f in result.findings if f.rule_id.startswith(group_prefix(path))]


def test_fixture_files_exist() -> None:
    names = {p.relative_to(FIXTURES).as_posix() for p in FIXTURE_FILES}
    assert {
        "cwd/positive_cwd.py",
        "cwd/negative_cwd.py",
        "env/positive_env.py",
        "env/negative_env.py",
        "exe/positive_exe.py",
        "exe/negative_exe.py",
        "plat/positive_plat.py",
        "plat/negative_plat.py",
        "imp/positive_imp.py",
        "imp/negative_imp.py",
    } <= names


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.relative_to(FIXTURES).as_posix())
def test_fixture_matches_markers(path: Path) -> None:
    expected = expected_findings(path)
    assert all(rule_id.startswith(group_prefix(path)) for _, rule_id in expected)
    findings = group_findings(path)
    assert all(f.location is not None for f in findings)
    actual: Counter[tuple[int, str]] = Counter(
        (f.location.line or 0, f.rule_id) for f in findings if f.location is not None
    )
    assert actual == expected
    if path.stem.startswith("negative"):
        assert findings == []
    if path.stem.startswith("positive"):
        assert findings
