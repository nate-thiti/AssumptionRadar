"""Tests for the CLI commands and exit codes (P1-14)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from assumptionradar import cli
from assumptionradar.cli import ExitCode, emit, main
from assumptionradar.core.catalog import SCENARIO_CATALOG
from assumptionradar.reporting.json_report import build_pytest_report


def verification(status: str, scenario_status: str | None = None) -> dict[str, Any]:
    return {
        "test_id": f"t.py::{status}",
        "status": status,
        "baseline": [
            {
                "outcome": "passed",
                "exit_code": 0,
                "duration_s": 0.1,
                "output_tail": "",
                "probe": None,
            }
        ],
        "scenarios": [
            {"scenario": "cwd", "status": scenario_status or status, "reason": "why", "run": None}
        ],
        "limitations": ["not a sandbox"],
    }


def write_report(tmp_path: Path, *statuses: str, observation: bool = False) -> Path:
    data = json.loads(json.dumps(build_pytest_report(None, [], [])))
    data["verifications"] = [verification(s) for s in statuses]
    if observation:
        data["observation"] = {"events": [], "dropped": 0, "unattributed": 0, "errors": []}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------- exit codes


def test_exit_code_values() -> None:
    assert [int(code) for code in ExitCode] == [0, 1, 2, 3, 4]
    assert [code.name for code in ExitCode] == [
        "OK",
        "FINDINGS",
        "USAGE",
        "INTERNAL",
        "INCONCLUSIVE",
    ]


# ----------------------------------------------------------------- scenarios


def test_scenarios_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenarios"]) == ExitCode.OK
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert lines[: len(SCENARIO_CATALOG)] == [
        f"{info.family:<10} {info.summary}" for info in SCENARIO_CATALOG
    ]
    assert lines[len(SCENARIO_CATALOG) :] == [
        "",
        "'default' expands to: cwd, tz, encoding, tmpdir",
        "Run 'assumptionradar explain <name>' for details.",
    ]
    assert out.err == ""


def test_scenarios_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenarios", "--format", "json"]) == ExitCode.OK
    out = capsys.readouterr()
    assert json.loads(out.out) == {
        "scenarios": [info.to_dict() for info in SCENARIO_CATALOG],
        "default": ["cwd", "tz", "encoding", "tmpdir"],
    }
    assert out.err == ""


# ------------------------------------------------------------------- explain


def test_explain_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "tz:+9"]) == ExitCode.OK
    assert capsys.readouterr().out.splitlines() == [
        "tz",
        "  use:     tz | tz:<zone>   (zones: +9, -5, utc)",
        "  changes: Run with a different local time zone, set through a POSIX TZ string.",
        "  reveals: Code that uses local time (datetime.now(), time.localtime(), naive "
        "datetimes) where it means a fixed time zone.",
        "  fix:     pass an explicit tzinfo (e.g. datetime.now(timezone.utc)) instead of "
        "relying on local time",
        "  note:    Plain 'tz' picks up to two zones whose offset differs from this machine's.",
        "  note:    Only POSIX TZ strings such as JST-9 are used: Windows silently ignores "
        "IANA names.",
    ]


def test_explain_without_notes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "cwd"]) == ExitCode.OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "cwd"
    assert [line.split(":")[0].strip() for line in lines[1:]] == [
        "use",
        "changes",
        "reveals",
        "fix",
    ]


def test_explain_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "env-unset:API_URL", "--format", "json"]) == ExitCode.OK
    info = next(i for i in SCENARIO_CATALOG if i.family == "env-unset")
    assert json.loads(capsys.readouterr().out) == info.to_dict()


def test_explain_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "nope"]) == ExitCode.USAGE
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == (
        "assumptionradar: unknown scenario 'nope'; "
        "known: cwd, tmpdir, tz, encoding, locale, env-unset\n"
    )


# --------------------------------------------------------------- report show


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((), ExitCode.OK),
        (("no_difference", "unsupported"), ExitCode.OK),
        (("no_difference", "inconclusive"), ExitCode.INCONCLUSIVE),
        (("error",), ExitCode.INCONCLUSIVE),
        (("inconclusive", "regression"), ExitCode.FINDINGS),
        (("regression",), ExitCode.FINDINGS),
    ],
)
def test_report_show_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    statuses: tuple[str, ...],
    expected: ExitCode,
) -> None:
    path = write_report(tmp_path, *statuses)
    assert main(["report", "show", str(path)]) == expected
    assert main(["report", "show", str(path), "--exit-zero"]) == ExitCode.OK
    capsys.readouterr()


def test_report_show_text_matches_renderer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_report(tmp_path, "regression", observation=True)
    main(["report", "show", str(path), "--color", "never"])
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        "verification: 1 regression (1 test)",
        "  REGRESSION    t.py::regression",
        "      cwd: regression (reproduced) - why",
        "        fix: build paths from Path(__file__).parent or a fixture instead of the "
        "working directory",
        "",
        "observation: 0 events; 0 unattributed, 0 dropped",
    ]
    assert out.err == ""


def test_report_show_colour_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_report(tmp_path, "regression")
    main(["report", "show", str(path), "--color", "always"])
    assert "\x1b[31m  REGRESSION" in capsys.readouterr().out
    monkeypatch.setenv("NO_COLOR", "1")
    main(["report", "show", str(path)])  # auto + NO_COLOR
    assert "\x1b[" not in capsys.readouterr().out
    main(["report", "show", str(path), "--color", "always"])  # explicit flag wins
    assert "\x1b[" in capsys.readouterr().out


def test_report_show_json_is_the_report_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_report(tmp_path, "regression")
    assert main(["report", "show", str(path), "--format", "json"]) == ExitCode.FINDINGS
    out = capsys.readouterr()
    assert json.loads(out.out) == json.loads(path.read_text(encoding="utf-8"))
    assert out.err == ""


def test_report_show_json_is_ascii_safe(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_report(tmp_path, "regression")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["verifications"][0]["scenarios"][0]["reason"] = "ไฟล์ทดสอบ"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    main(["report", "show", str(path), "--format", "json"])
    out = capsys.readouterr().out
    assert out.isascii()
    assert json.loads(out)["verifications"][0]["scenarios"][0]["reason"] == "ไฟล์ทดสอบ"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "cannot read report"),
        ("{not json", "is not valid JSON"),
        (b"\xff\xfe\x00bad", "is not valid JSON"),
        ("[1, 2]", "not an AssumptionRadar report (expected a JSON object)"),
        (
            '{"schema_version": "2"}',
            "unsupported report schema_version '2'; this version reads '1'",
        ),
        ("{}", "unsupported report schema_version None; this version reads '1'"),
        ('{"schema_version": "1"}', "report does not match schema v1"),
        (
            '{"schema_version": "1", "tool": {}, "platform": {}, "observation": null, '
            '"verifications": [{"status": "regression"}], "verification_skipped": []}',
            "report does not match schema v1",
        ),
        (
            '{"schema_version": "1", "tool": {}, "platform": {}, "observation": [], '
            '"verifications": [], "verification_skipped": []}',
            "report does not match schema v1",
        ),
    ],
)
def test_report_show_bad_input_is_usage_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str | bytes | None,
    message: str,
) -> None:
    path = tmp_path / "r.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content, encoding="utf-8")
    assert main(["report", "show", str(path)]) == ExitCode.USAGE
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith("assumptionradar: ")
    assert message in out.err
    assert out.err.count("\n") == 1  # one line, no traceback


def test_report_without_subcommand_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["report"]) == ExitCode.USAGE
    capsys.readouterr()


def test_unknown_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["frobnicate"]) == ExitCode.USAGE
    assert "invalid choice" in capsys.readouterr().err


# ------------------------------------------------------------ internal error


def test_unexpected_exception_is_internal_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "render_report", boom)
    path = write_report(tmp_path, "regression")
    assert main(["report", "show", str(path)]) == ExitCode.INTERNAL
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "assumptionradar: internal error: RuntimeError: kaboom\n"


# ---------------------------------------------------------------------- emit


class _AsciiStream(io.StringIO):
    encoding = "ascii"

    def write(self, s: str) -> int:
        s.encode("ascii")  # raises UnicodeEncodeError like a cp874/ascii console
        return super().write(s)


def test_emit_replaces_unencodable_characters() -> None:
    stream = _AsciiStream()
    emit("ok ไฟล์\n", stream)
    assert stream.getvalue() == "ok ????\n"


def test_emit_plain() -> None:
    stream = io.StringIO()
    emit("ไฟล์\n", stream)
    assert stream.getvalue() == "ไฟล์\n"


# ---------------------------------------------------------- real subprocess


def test_module_report_show_json_stdout_is_pure(tmp_path: Path) -> None:
    path = write_report(tmp_path, "inconclusive")
    result = subprocess.run(
        [sys.executable, "-m", "assumptionradar", "report", "show", str(path), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == ExitCode.INCONCLUSIVE
    assert json.loads(result.stdout)["verifications"][0]["status"] == "inconclusive"
    assert result.stderr == ""
