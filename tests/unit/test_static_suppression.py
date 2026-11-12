"""Tests for inline suppression comments (P2-7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from assumptionradar.static.cache import FileCache
from assumptionradar.static.engine import scan
from assumptionradar.static.rules import default_rules
from assumptionradar.static.suppression import (
    Suppression,
    SuppressionProblem,
    parse_suppressions,
)

MALFORMED = (
    "malformed suppression comment; expected '# assumptionradar: ignore[RULE-ID, ...] -- reason'"
)

# ---------------------------------------------------------------------- parsing


def test_parse_specific_rule_with_reason() -> None:
    source = "open('x')  # assumptionradar: ignore[AR-CWD-001] -- runs from the repo root\n"
    suppressions, malformed = parse_suppressions(source)
    assert suppressions == [Suppression(1, 12, ("AR-CWD-001",), "runs from the repo root")]
    assert malformed == []


@pytest.mark.parametrize(
    ("comment", "rule_ids", "reason"),
    [
        ("# assumptionradar: ignore[AR-CWD-001, AR-ENV-001]", ("AR-CWD-001", "AR-ENV-001"), None),
        ("#assumptionradar:ignore[AR-CWD-001]--why", ("AR-CWD-001",), "why"),
        ("# AssumptionRadar: ignore[AR-CWD-001]", ("AR-CWD-001",), None),
        ("# assumptionradar: ignore", None, None),
        ("# assumptionradar: ignore -- generated file", None, "generated file"),
        ("# assumptionradar: ignore[AR-CWD-001] --   ", ("AR-CWD-001",), None),
        (
            "# assumptionradar: ignore[ AR-CWD-001 ,AR-PLAT-001 ]",
            ("AR-CWD-001", "AR-PLAT-001"),
            None,
        ),
    ],
)
def test_parse_variants(comment: str, rule_ids: tuple[str, ...] | None, reason: str | None) -> None:
    suppressions, malformed = parse_suppressions(f"x = 1  {comment}\n")
    assert malformed == []
    assert [(s.line, s.rule_ids, s.reason) for s in suppressions] == [(1, rule_ids, reason)]


def test_comment_on_its_own_line_and_column() -> None:
    suppressions, _ = parse_suppressions("x = 1\n    # assumptionradar: ignore[AR-CWD-001]\n")
    assert [(s.line, s.column) for s in suppressions] == [(2, 5)]


@pytest.mark.parametrize(
    ("comment", "message"),
    [
        ("# assumptionradar: ignroe[AR-CWD-001]", MALFORMED),
        ("# assumptionradar: ignore[]", MALFORMED),
        ("# assumptionradar: ignore[AR-CWD-001", MALFORMED),
        ("# assumptionradar:", MALFORMED),
        ("# assumptionradar: ignore[cwd]", "invalid rule id 'cwd' in suppression comment"),
        (
            "# assumptionradar: ignore[AR-CWD-001,,AR-ENV-001]",
            "invalid rule id '' in suppression comment",
        ),
    ],
)
def test_malformed_comments(comment: str, message: str) -> None:
    suppressions, malformed = parse_suppressions(f"x = 1\ny = 2  {comment}\n")
    assert suppressions == []
    assert malformed == [(2, message)]


def test_other_comments_and_strings_are_ignored() -> None:
    source = (
        "s = '# assumptionradar: ignore[AR-CWD-001]'\n"
        "t = 1  # noqa: E501\n"
        "u = 2  # assumptionradarx: ignore\n"
        "v = 3  # see assumptionradar: ignore docs\n"
    )
    assert parse_suppressions(source) == ([], [])


def test_untokenizable_source_yields_nothing() -> None:
    assert parse_suppressions("x = '''unterminated\n") == ([], [])


# ----------------------------------------------------------------------- engine


def project(tmp_path: Path, source: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    return tmp_path


def lines(findings: tuple[object, ...]) -> list[tuple[int, str]]:
    out = []
    for f in findings:
        location = getattr(f, "location", None)
        assert location is not None
        out.append((location.line, getattr(f, "rule_id", "")))
    return out


def test_specific_suppression_moves_finding(tmp_path: Path) -> None:
    root = project(tmp_path, "open('a')  # assumptionradar: ignore[AR-CWD-001] -- ok\nopen('b')\n")
    result = scan(root, default_rules())
    assert lines(result.findings) == [(2, "AR-CWD-001")]
    assert lines(result.suppressed) == [(1, "AR-CWD-001")]
    assert result.suppression_problems == ()


def test_wrong_rule_does_not_suppress_and_is_stale(tmp_path: Path) -> None:
    root = project(tmp_path, "open('a')  # assumptionradar: ignore[AR-ENV-001]\n")
    result = scan(root, default_rules())
    assert lines(result.findings) == [(1, "AR-CWD-001")]
    assert result.suppressed == ()
    assert result.suppression_problems == (
        SuppressionProblem(
            "m.py",
            1,
            "stale",
            "AR-ENV-001 is suppressed on line 1 but no AR-ENV-001 finding is reported there",
            "AR-ENV-001",
        ),
    )


def test_partly_stale_multi_rule_suppression(tmp_path: Path) -> None:
    root = project(tmp_path, "open('a')  # assumptionradar: ignore[AR-CWD-001, AR-EXE-001]\n")
    result = scan(root, default_rules())
    assert result.findings == ()
    assert lines(result.suppressed) == [(1, "AR-CWD-001")]
    assert [(p.kind, p.rule_id) for p in result.suppression_problems] == [("stale", "AR-EXE-001")]


def test_bare_suppression_non_strict_and_strict(tmp_path: Path) -> None:
    root = project(tmp_path, "open('a')  # assumptionradar: ignore\n")
    relaxed = scan(root, default_rules())
    assert relaxed.findings == ()
    assert lines(relaxed.suppressed) == [(1, "AR-CWD-001")]
    assert relaxed.suppression_problems == ()

    strict = scan(root, default_rules(), strict_suppressions=True)
    assert lines(strict.findings) == [(1, "AR-CWD-001")]
    assert strict.suppressed == ()
    assert strict.suppression_problems == (
        SuppressionProblem(
            "m.py",
            1,
            "bare",
            "suppression on line 1 does not name a rule; strict mode requires ignore[RULE-ID]",
            None,
        ),
    )


def test_stale_bare_suppression(tmp_path: Path) -> None:
    root = project(tmp_path, "x = 1  # assumptionradar: ignore\n")
    result = scan(root, default_rules())
    assert result.suppression_problems == (
        SuppressionProblem("m.py", 1, "stale", "suppression on line 1 matches no finding", None),
    )


def test_inactive_rule_is_not_stale(tmp_path: Path) -> None:
    root = project(tmp_path, "x = 1  # assumptionradar: ignore[AR-IMP-001, AR-XYZ-999]\n")
    assert scan(root, default_rules()).suppression_problems == ()


def test_malformed_comment_is_a_problem(tmp_path: Path) -> None:
    root = project(tmp_path, "open('a')  # assumptionradar: ignore[cwd]\n")
    result = scan(root, default_rules())
    assert lines(result.findings) == [(1, "AR-CWD-001")]
    assert result.suppression_problems == (
        SuppressionProblem(
            "m.py", 1, "malformed", "invalid rule id 'cwd' in suppression comment", None
        ),
    )


def test_fingerprints_do_not_shift_when_suppressing(tmp_path: Path) -> None:
    plain = scan(project(tmp_path / "a", "open('x')\nopen('x')\n"), default_rules())
    marked = scan(
        project(tmp_path / "b", "open('x')  # assumptionradar: ignore[AR-CWD-001]\nopen('x')\n"),
        default_rules(),
    )
    assert [f.fingerprint for f in marked.suppressed] == [plain.findings[0].fingerprint]
    assert [f.fingerprint for f in marked.findings] == [plain.findings[1].fingerprint]
    assert marked.findings[0].fingerprint.endswith(":2")


def test_suppression_applies_to_cached_results(tmp_path: Path) -> None:
    root = project(tmp_path / "p", "open('a')  # assumptionradar: ignore[AR-CWD-001]\n")
    cache_dir = tmp_path / "cache"
    first = scan(root, default_rules(), cache=FileCache(cache_dir, "t"))
    second = scan(root, default_rules(), cache=FileCache(cache_dir, "t"))
    assert second.cache_hits == 1
    assert (second.findings, second.suppressed) == (first.findings, first.suppressed)
    assert second.findings == ()


def test_problems_are_sorted_and_serialisable(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("x = 1  # assumptionradar: ignore\n", encoding="utf-8")
    (tmp_path / "a.py").write_text(
        "x = 1  # assumptionradar: bad\ny = 2  # assumptionradar: ignore\n", encoding="utf-8"
    )
    result = scan(tmp_path, default_rules())
    assert [(p.relpath, p.line, p.kind) for p in result.suppression_problems] == [
        ("a.py", 1, "malformed"),
        ("a.py", 2, "stale"),
        ("b.py", 1, "stale"),
    ]
    assert result.suppression_problems[0].to_dict() == {
        "relpath": "a.py",
        "line": 1,
        "kind": "malformed",
        "message": MALFORMED,
        "rule_id": None,
    }


def test_files_with_syntax_errors_have_no_suppression_problems(tmp_path: Path) -> None:
    root = project(tmp_path, "def f(:  # assumptionradar: ignore\n")
    result = scan(root, default_rules())
    assert [e.kind for e in result.errors] == ["syntax"]
    assert result.suppression_problems == ()
