"""Tests for ``assumptionradar scan`` and the scan report builders (P2-11)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import assumptionradar
from assumptionradar.cli import ExitCode, main
from assumptionradar.config import Settings
from assumptionradar.reporting.sarif import SARIF_VERSION
from assumptionradar.reporting.scan_report import (
    SCAN_REPORT_KIND,
    build_scan_report,
    is_failing,
    render_scan,
)
from assumptionradar.static.engine import scan
from assumptionradar.static.rules import default_rules

SOURCE = "import os\ndef load():\n    return open('config.json')\nos.environ['TOKEN']\n"


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def project(tmp_path: Path, files: dict[str, str] | None = None, config: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    for name, text in (files if files is not None else {"m.py": SOURCE}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if config is not None:
        (root / "assumptionradar.toml").write_text(config, encoding="utf-8")
    return root


# ------------------------------------------------------------------------ text


def test_text_output_lists_findings_and_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    code, out, err = run(["scan", str(root), "--color", "never"], capsys)
    assert code == ExitCode.FINDINGS
    assert err == ""
    lines = out.splitlines()
    assert lines[0].startswith("m.py:3:12: AR-CWD-001 [warning] ")
    assert lines[1].startswith("m.py:4:1: AR-ENV-001 [warning] ")
    assert lines[-1] == "2 findings in 1 file"
    assert len(lines) == 3
    assert "\x1b[" not in out


def test_clean_project_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    code, out, _ = run(["scan", str(root)], capsys)
    assert (code, out) == (ExitCode.OK, "0 findings in 2 files\n")


def test_summary_extras(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(
        tmp_path,
        {
            "m.py": "open('a.json')  # assumptionradar: ignore[AR-CWD-001]\n"
            "x = 1  # assumptionradar: ignore[AR-ENV-001]\n",
            "bad.py": "def f(:\n",
        },
    )
    code, out, _ = run(["scan", str(root)], capsys)
    assert code == ExitCode.OK
    lines = out.splitlines()
    assert lines == [
        "m.py:2: stale suppression: AR-ENV-001 is suppressed on line 2 but no AR-ENV-001 "
        "finding is reported there",
        lines[1],
        "0 findings in 1 file; 1 suppressed, 1 file not scanned, 1 suppression problem",
    ]
    assert lines[1].startswith("bad.py:1: could not be scanned (syntax): ")


def test_verbose_adds_fixes_suppressed_and_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path,
        {
            "m.py": "open('a.json')\nopen('b.json')  # assumptionradar: ignore[AR-CWD-001]\n",
            "big.py": "x = 1\n" * 50,
        },
        config="max-file-bytes = 100\n",
    )
    code, out, _ = run(["scan", str(root), "-v"], capsys)
    assert code == ExitCode.FINDINGS
    lines = out.splitlines()
    assert lines[0].startswith("m.py:1:1: AR-CWD-001 [warning] ")
    assert lines[1].startswith("    fix: ")
    assert lines[2].startswith("m.py:2:1: AR-CWD-001 [suppressed] ")
    assert lines[3].startswith("big.py: skipped: ")
    assert lines[4] == "1 finding in 1 file; 1 suppressed"


def test_quiet_prints_only_problems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code, out, _ = run(["scan", str(root), "-q"], capsys)
    assert code == ExitCode.FINDINGS
    assert [line.split()[1] for line in out.splitlines()] == ["AR-CWD-001", "AR-ENV-001"]

    clean = project(tmp_path / "c", {"a.py": "x = 1\n"})
    code, out, _ = run(["scan", str(clean), "--quiet"], capsys)
    assert (code, out) == (ExitCode.OK, "")


def test_quiet_and_verbose_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run(["scan", str(project(tmp_path)), "-q", "-v"], capsys)
    assert code == ExitCode.USAGE


def test_color_always(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    _, out, _ = run(["scan", str(root), "--color", "always"], capsys)
    lines = out.splitlines()
    assert lines[0].startswith("\x1b[33mm.py:3:12: AR-CWD-001")  # warning: yellow
    assert lines[-1] == "\x1b[31m2 findings in 1 file\x1b[0m"  # failing summary: red

    clean = project(tmp_path / "c", {"a.py": "x = 1\n"})
    _, out, _ = run(["scan", str(clean), "--color", "always"], capsys)
    assert out == "\x1b[32m0 findings in 1 file\x1b[0m\n"


# ----------------------------------------------------------------- thresholds


def test_fail_on_from_option_and_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    assert run(["scan", str(root), "--fail-on", "error"], capsys)[0] == ExitCode.OK
    (root / "assumptionradar.toml").write_text('fail-on = "error"\n', encoding="utf-8")
    assert run(["scan", str(root)], capsys)[0] == ExitCode.OK
    assert run(["scan", str(root), "--fail-on", "warning"], capsys)[0] == ExitCode.FINDINGS


def test_info_findings_need_fail_on_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path, {"m.py": "import os\nos.environ.get('X')\n"}, config="[env]\ndeclared = []\n"
    )
    code, out, _ = run(["scan", str(root)], capsys)
    assert code == ExitCode.OK
    assert "AR-ENV-002 [info]" in out
    assert run(["scan", str(root), "--fail-on", "info"], capsys)[0] == ExitCode.FINDINGS


def test_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["scan", str(project(tmp_path)), "--exit-zero"], capsys)
    assert code == ExitCode.OK
    assert "2 findings" in out


def test_strict_suppressions_fail_on_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"m.py": "x = 1  # assumptionradar: ignore\n"})
    assert run(["scan", str(root)], capsys)[0] == ExitCode.OK
    code, out, _ = run(["scan", str(root), "--strict-suppressions"], capsys)
    assert code == ExitCode.FINDINGS
    assert "bare suppression" in out
    (root / "assumptionradar.toml").write_text("strict-suppressions = true\n", encoding="utf-8")
    assert run(["scan", str(root)], capsys)[0] == ExitCode.FINDINGS


def test_config_selects_rules(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, config='ignore-rules = ["AR-ENV-001"]\n')
    _, out, _ = run(["scan", str(root)], capsys)
    assert "AR-ENV-001" not in out
    assert out.splitlines()[-1] == "1 finding in 1 file"


# ---------------------------------------------------------------- json / sarif


def test_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, {"m.py": SOURCE, "bad.py": "def f(:\n"})
    code, out, _ = run(["scan", str(root), "--format", "json"], capsys)
    assert code == ExitCode.FINDINGS
    data = json.loads(out)
    assert list(data) == [
        "schema_version",
        "kind",
        "tool",
        "root",
        "config_file",
        "summary",
        "findings",
        "suppressed",
        "suppression_problems",
        "errors",
        "skipped",
    ]
    assert data["schema_version"] == "1"
    assert data["kind"] == SCAN_REPORT_KIND == "assumptionradar-scan"
    assert data["tool"] == {"name": "assumptionradar", "version": assumptionradar.__version__}
    assert data["root"] == str(root)
    assert data["config_file"] is None
    assert data["summary"] == {
        "files_scanned": 1,
        "findings": 2,
        "suppressed": 0,
        "suppression_problems": 0,
        "errors": 1,
        "skipped": 0,
        "by_severity": {"error": 0, "warning": 2, "info": 0},
        "fail_on": "warning",
        "failing": True,
    }
    assert [f["rule_id"] for f in data["findings"]] == ["AR-CWD-001", "AR-ENV-001"]
    assert data["errors"][0]["relpath"] == "bad.py"
    assert data["errors"][0]["kind"] == "syntax"


def test_format_from_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, config='format = "json"\n')
    _, out, _ = run(["scan", str(root)], capsys)
    data = json.loads(out)
    assert data["config_file"] == str(root / "assumptionradar.toml")
    _, out, _ = run(["scan", str(root), "--format", "text"], capsys)
    assert out.endswith("2 findings in 1 file\n")


def test_sarif_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code, out, _ = run(["scan", str(root), "--format", "sarif"], capsys)
    assert code == ExitCode.FINDINGS
    data = json.loads(out)
    assert data["version"] == SARIF_VERSION
    run_data = data["runs"][0]
    assert len(run_data["results"]) == 2
    assert run_data["originalUriBaseIds"]["%SRCROOT%"]["uri"] == root.absolute().as_uri() + "/"


def test_json_skipped_paths_are_relative(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"pkg/big.py": "x = 1\n" * 50}, config="max-file-bytes = 100\n")
    _, out, _ = run(["scan", str(root), "--format", "json"], capsys)
    (item,) = json.loads(out)["skipped"]
    assert item["path"] == "pkg/big.py"
    assert item["reason"]


# --------------------------------------------------------------------- output


@pytest.mark.parametrize("fmt", ["text", "json", "sarif"])
def test_output_file(tmp_path: Path, capsys: pytest.CaptureFixture[str], fmt: str) -> None:
    root = project(tmp_path)
    target = tmp_path / f"report.{fmt}"
    code, out, _ = run(
        ["scan", str(root), "--format", fmt, "--output", str(target), "--color", "always"], capsys
    )
    assert code == ExitCode.FINDINGS
    assert out == f"report written: {target}\n"
    text = target.read_text(encoding="utf-8")
    assert "\x1b[" not in text
    assert text.endswith("\n")
    if fmt == "text":
        assert text.endswith("2 findings in 1 file\n")
    else:
        json.loads(text)


def test_output_file_quiet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "r.json"
    code, out, _ = run(
        ["scan", str(project(tmp_path)), "--format", "json", "-o", str(target), "-q"], capsys
    )
    assert (code, out) == (ExitCode.FINDINGS, "")
    assert target.is_file()


def test_unwritable_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "missing-dir" / "r.json"
    code, _, err = run(["scan", str(project(tmp_path)), "--output", str(target)], capsys)
    assert code == ExitCode.USAGE
    assert err.startswith(f"assumptionradar: cannot write {target}: ")


# --------------------------------------------------------------------- errors


def test_bad_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, config="excludes = []\n")
    code, out, err = run(["scan", str(root)], capsys)
    assert code == ExitCode.USAGE
    assert out == ""
    assert "did you mean 'exclude'?" in err


def test_missing_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["scan", str(tmp_path / "nope")], capsys)
    assert code == ExitCode.USAGE
    assert "is not a directory" in err


def test_no_cache_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    run(["scan", str(root), "--no-cache"], capsys)
    assert not (root / ".assumptionradar_cache").exists()
    run(["scan", str(root)], capsys)
    assert (root / ".assumptionradar_cache").is_dir()


def test_explicit_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    custom = tmp_path / "other.toml"
    custom.write_text('rules = ["AR-ENV-001"]\n', encoding="utf-8")
    _, out, _ = run(["scan", str(root), "--config", str(custom)], capsys)
    assert out.splitlines()[-1] == "1 finding in 1 file"


# ------------------------------------------------------------ library helpers


def test_is_failing_helper(tmp_path: Path) -> None:
    root = project(tmp_path)
    result = scan(root, default_rules())
    assert is_failing(result, Settings()) is True
    assert is_failing(result, Settings(fail_on="error")) is False


def test_build_scan_report_and_render(tmp_path: Path) -> None:
    root = project(tmp_path)
    result = scan(root, default_rules())
    report = build_scan_report(result, root=root, settings=Settings())
    assert report["summary"]["findings"] == 2  # type: ignore[index]
    lines = render_scan(result, settings=Settings())
    assert lines[-1].text == "2 findings in 1 file"
    assert lines[-1].style == "red"
