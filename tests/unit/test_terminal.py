"""Snapshot tests for the terminal renderer (P1-13).

The renderer turns a v1 report dict (the same document ``--assumption-report``
writes) into styled lines, so the pytest plugin and ``report show`` share it.
"""

from __future__ import annotations

import io
from typing import Any

import jsonschema
import pytest

from assumptionradar.reporting.json_report import load_report_schema
from assumptionradar.reporting.terminal import (
    ANSI_RESET,
    ANSI_STYLES,
    Line,
    fix_hint,
    render_report,
    to_text,
    use_color,
)


def loc(path: str, line: int | None = None) -> dict[str, Any]:
    return {"path": path, "line": line, "column": None, "symbol": None}


def event(
    kind: str,
    target: str,
    attributes: dict[str, str],
    location: dict[str, Any] | None,
    test_id: str | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "target": target,
        "attributes": attributes,
        "location": location,
        "test_id": test_id,
    }


def run() -> dict[str, Any]:
    return {
        "outcome": "passed",
        "exit_code": 0,
        "duration_s": 0.1,
        "output_tail": "",
        "probe": None,
    }


def verification(
    test_id: str, status: str, scenarios: list[tuple[str, str, str]]
) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "status": status,
        "baseline": [run()],
        "scenarios": [
            {"scenario": name, "status": st, "reason": reason, "run": None}
            for name, st, reason in scenarios
        ],
        "limitations": ["a child process is not a sandbox"],
    }


def report(
    observation: dict[str, Any] | None = None,
    verifications: list[dict[str, Any]] | None = None,
    skipped: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    data = {
        "schema_version": "1",
        "tool": {"name": "assumptionradar", "version": "0.1.0"},
        "platform": {"system": "win32", "python": "3.14.0"},
        "observation": observation,
        "verifications": verifications or [],
        "verification_skipped": skipped or [],
    }
    jsonschema.validate(data, load_report_schema())  # fixtures must be realistic
    return data


EVENTS = [
    event(
        "env_read",
        "HOME",
        {"present": "true"},
        loc("tests/test_a.py", 4),
        "tests/test_a.py::test_a",
    ),
    event(
        "file_open",
        "data.txt",
        {"relative": "true", "mode": "r"},
        loc("tests/test_a.py", 3),
        "tests/test_a.py::test_a",
    ),
    event(
        "subprocess",
        "git",
        {"argc": "2", "path_lookup": "true", "shell": "false", "env_override": "false"},
        None,
        None,
    ),
    event(
        "env_read", "API_URL", {"present": "false"}, loc("conftest.py"), "tests/test_b.py::test_b"
    ),
]

OBSERVATION = {"events": EVENTS, "dropped": 0, "unattributed": 3, "errors": []}

VERIFICATIONS = [
    verification("tests/test_ok.py::test_ok", "no_difference", [("cwd", "no_difference", "same")]),
    verification(
        "tests/test_a.py::test_a",
        "regression",
        [
            ("cwd", "regression", "passed in baseline, failed under scenario"),
            ("tz:JST-9", "no_difference", "same"),
            ("locale:de_DE", "unsupported", "locale not installed"),
        ],
    ),
    verification("tests/test_t.py::test_t", "inconclusive", [("cwd", "inconclusive", "timed out")]),
]

SKIPPED = [{"test_id": "tests/test_bad.py::test_bad", "reason": "did not pass in this session"}]

CWD_FIX = "build paths from Path(__file__).parent or a fixture instead of the working directory"


# ------------------------------------------------------------------ snapshots


def test_full_report_snapshot() -> None:
    lines = render_report(report(OBSERVATION, VERIFICATIONS, SKIPPED))
    assert lines == [
        Line("verification: 1 regression, 1 inconclusive, 1 no_difference (3 tests)", "bold"),
        Line("  REGRESSION    tests/test_a.py::test_a", "red"),
        Line("      cwd: regression (reproduced) - passed in baseline, failed under scenario"),
        Line(f"        fix: {CWD_FIX}"),
        Line("      locale:de_DE: unsupported - locale not installed"),
        Line("  INCONCLUSIVE  tests/test_t.py::test_t", "yellow"),
        Line("      cwd: inconclusive - timed out"),
        Line("  NO_DIFFERENCE tests/test_ok.py::test_ok", "green"),
        Line("  skipped 1 test:"),
        Line("    tests/test_bad.py::test_bad - did not pass in this session"),
        Line(""),
        Line(
            "observation: 4 events (file_open: 1, env_read: 2, subprocess: 1); "
            "3 unattributed, 0 dropped",
            "bold",
        ),
        Line(
            "  ! file_open data.txt [mode=r, relative=true] at tests/test_a.py:3 "
            "(tests/test_a.py::test_a)",
            "yellow",
        ),
        Line(
            "  ! subprocess git [argc=2, env_override=false, path_lookup=true, shell=false] "
            "at unknown location (collection)",
            "yellow",
        ),
        Line(
            "  ! env_read API_URL [present=false] at conftest.py (tests/test_b.py::test_b)",
            "yellow",
        ),
        Line("    env_read HOME [present=true] at tests/test_a.py:4 (tests/test_a.py::test_a)"),
    ]


def test_empty_report() -> None:
    assert render_report(report()) == [Line("nothing observed or verified")]


def test_observation_only_without_events() -> None:
    obs = {"events": [], "dropped": 0, "unattributed": 0, "errors": []}
    assert render_report(report(obs)) == [
        Line("observation: 0 events; 0 unattributed, 0 dropped", "bold"),
    ]


def test_single_event_singular_and_no_attributes() -> None:
    obs = {
        "events": [event("chdir", "sub", {}, loc("tests/t.py", 1), "tests/t.py::t")],
        "dropped": 5,
        "unattributed": 0,
        "errors": [],
    }
    assert render_report(report(obs)) == [
        Line("observation: 1 event (chdir: 1); 0 unattributed, 5 dropped", "bold"),
        Line("    chdir sub at tests/t.py:1 (tests/t.py::t)"),
    ]


def test_observer_errors() -> None:
    obs = {"events": [], "dropped": 0, "unattributed": 0, "errors": ["boom", "bang"]}
    assert render_report(report(obs)) == [
        Line("observation: 0 events; 0 unattributed, 0 dropped", "bold"),
        Line("  2 internal observer errors; first: boom", "red"),
    ]
    one = {"events": [], "dropped": 0, "unattributed": 0, "errors": ["boom"]}
    assert render_report(report(one))[1] == Line("  1 internal observer error; first: boom", "red")


def test_event_limit_keeps_attention_events_first() -> None:
    lines = render_report(report(OBSERVATION), max_events=2)
    assert [line.text for line in lines[1:]] == [
        "  ! file_open data.txt [mode=r, relative=true] at tests/test_a.py:3 "
        "(tests/test_a.py::test_a)",
        "  ! subprocess git [argc=2, env_override=false, path_lookup=true, shell=false] "
        "at unknown location (collection)",
        "  ... 2 more events; see the JSON report for all",
    ]


def test_skipped_only_and_skip_limit() -> None:
    skipped = [{"test_id": f"t.py::t{i}", "reason": "verification limit reached"} for i in range(3)]
    assert render_report(report(skipped=skipped), max_skipped=2) == [
        Line("verification: no tests verified", "bold"),
        Line("  skipped 3 tests:"),
        Line("    t.py::t0 - verification limit reached"),
        Line("    t.py::t1 - verification limit reached"),
        Line("    ... 1 more"),
    ]


@pytest.mark.parametrize(
    ("status", "style"),
    [
        ("regression", "red"),
        ("error", "red"),
        ("inconclusive", "yellow"),
        ("unsupported", "yellow"),
        ("no_difference", "green"),
        ("not_verified", None),
    ],
)
def test_status_styles(status: str, style: str | None) -> None:
    v = verification("t.py::t", status, [("cwd", status, "why")])
    lines = render_report(report(verifications=[v]))
    assert lines[1].style == style
    assert lines[1].text == f"  {status.upper():<14}t.py::t"


def test_verifications_sorted_by_severity_then_session_order() -> None:
    order = ["no_difference", "unsupported", "regression", "error", "inconclusive", "regression"]
    vs = [verification(f"t.py::t{i}", s, [("cwd", s, "x")]) for i, s in enumerate(order)]
    lines = render_report(report(verifications=vs))
    heads = [line.text.split()[-1] for line in lines if line.style in {"red", "yellow", "green"}]
    assert heads == ["t.py::t2", "t.py::t5", "t.py::t3", "t.py::t4", "t.py::t1", "t.py::t0"]
    assert lines[0].text == (
        "verification: 2 regression, 1 error, 1 inconclusive, 1 unsupported, "
        "1 no_difference (6 tests)"
    )


def test_regression_without_known_hint_has_no_fix_line() -> None:
    v = verification("t.py::t", "regression", [("custom", "regression", "failed")])
    assert render_report(report(verifications=[v])) == [
        Line("verification: 1 regression (1 test)", "bold"),
        Line("  REGRESSION    t.py::t", "red"),
        Line("      custom: regression (reproduced) - failed"),
    ]


def test_render_does_not_mutate_report() -> None:
    data = report(OBSERVATION, VERIFICATIONS, SKIPPED)
    snapshot = repr(data)
    render_report(data, max_events=1, max_skipped=1)
    assert repr(data) == snapshot


# ---------------------------------------------------------------------- hints


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("cwd", CWD_FIX),
        ("tmpdir", "use the tmp_path fixture instead of the system temporary directory"),
        (
            "tz:JST-9",
            "pass an explicit tzinfo (e.g. datetime.now(timezone.utc)) instead of relying "
            "on local time",
        ),
        ("encoding:legacy", "pass encoding= explicitly to open(), read_text() and write_text()"),
        (
            "locale:de_DE",
            "avoid locale-dependent formatting, or set the locale explicitly in a fixture",
        ),
        (
            "env-unset:API_URL",
            "give the variable a default, or set it in a fixture with monkeypatch.setenv",
        ),
        ("custom", None),
        ("", None),
    ],
)
def test_fix_hint(scenario: str, expected: str | None) -> None:
    assert fix_hint(scenario) == expected


# --------------------------------------------------------------------- colour


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _BrokenStream(io.StringIO):
    def isatty(self) -> bool:
        raise ValueError("I/O operation on closed file")


@pytest.mark.parametrize(
    ("tty", "environ", "expected"),
    [
        (True, {}, True),
        (False, {}, False),
        (True, {"NO_COLOR": "1"}, False),
        (True, {"NO_COLOR": ""}, True),  # https://no-color.org: only a non-empty value counts
        (False, {"FORCE_COLOR": "1"}, True),
        (False, {"FORCE_COLOR": ""}, False),
        (True, {"NO_COLOR": "1", "FORCE_COLOR": "1"}, False),  # NO_COLOR wins
    ],
)
def test_use_color(tty: bool, environ: dict[str, str], expected: bool) -> None:
    assert use_color(_Stream(tty), environ) is expected


def test_use_color_without_isatty_or_broken_stream() -> None:
    assert use_color(object(), {}) is False
    assert use_color(_BrokenStream(), {}) is False


def test_use_color_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert use_color(_Stream(True)) is False


def test_to_text_plain_and_coloured() -> None:
    lines = [Line("bad", "red"), Line("plain"), Line("")]
    assert to_text(lines, color=False) == "bad\nplain\n\n"
    assert to_text(lines, color=True) == f"{ANSI_STYLES['red']}bad{ANSI_RESET}\nplain\n\n"
    assert to_text([], color=True) == ""


def test_ansi_styles() -> None:
    assert ANSI_STYLES == {
        "red": "\x1b[31m",
        "yellow": "\x1b[33m",
        "green": "\x1b[32m",
        "bold": "\x1b[1m",
    }
    assert ANSI_RESET == "\x1b[0m"


def test_line_rejects_unknown_style() -> None:
    with pytest.raises(ValueError, match="unknown style"):
        Line("x", "blue")
