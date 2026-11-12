"""Tests for ``assumptionradar config check`` (P2-8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assumptionradar.cli import ExitCode, main


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_config_check_without_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".git").mkdir()
    code, out, err = run(["config", "check", str(tmp_path)], capsys)
    assert code == ExitCode.OK
    assert out == "config OK: no config file found, using defaults\n"
    assert err == ""


def test_config_check_with_pyproject(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.assumptionradar]\nfail-on = "error"\n', encoding="utf-8")
    code, out, _ = run(["config", "check", str(tmp_path)], capsys)
    assert code == ExitCode.OK
    assert out == f"config OK: {path}\n"


def test_config_check_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.assumptionradar]\nexclude = ["build/**"]\n[tool.assumptionradar.env]\n'
        'declared = ["HOME"]\n',
        encoding="utf-8",
    )
    code, out, _ = run(["config", "check", str(tmp_path), "--format", "json"], capsys)
    assert code == ExitCode.OK
    data = json.loads(out)
    assert data["config_file"] == str(path)
    assert data["exclude"] == ["build/**"]
    assert data["env_declared"] == ["HOME"]
    assert data["rules"] is None
    assert data["fail_on"] == "warning"
    assert data["active_rules"] == [
        "AR-CWD-001",
        "AR-ENV-001",
        "AR-ENV-002",
        "AR-EXE-001",
        "AR-PLAT-001",
        "AR-IMP-001",  # a pyproject.toml exists, so the project-aware rule is active
    ]


@pytest.mark.parametrize(
    "body",
    [
        "[tool.assumptionradar\n",
        "[tool.assumptionradar]\nexcludes = []\n",
        '[tool.assumptionradar]\nfail-on = "loud"\n',
        "[tool.assumptionradar]\nmax-file-bytes = -1\n",
        '[tool.assumptionradar]\nrules = ["AR-NOPE-001"]\n',
        "[tool.assumptionradar.env]\ndeclared = 3\n",
    ],
)
def test_bad_config_exits_2_with_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(body, encoding="utf-8")
    code, out, err = run(["config", "check", str(tmp_path)], capsys)
    assert code == ExitCode.USAGE
    assert out == ""
    assert err.startswith(f"assumptionradar: {tmp_path / 'pyproject.toml'}: ")
    assert err.endswith("\n")
    assert "Traceback" not in err


def test_explicit_config_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    custom = tmp_path / "custom.toml"
    custom.write_text('format = "sarif"\n', encoding="utf-8")
    code, out, _ = run(["config", "check", str(tmp_path), "--config", str(custom)], capsys)
    assert (code, out) == (ExitCode.OK, f"config OK: {custom}\n")
    code, _, err = run(
        ["config", "check", str(tmp_path), "--config", str(tmp_path / "missing.toml")], capsys
    )
    assert code == ExitCode.USAGE
    assert "config file not found" in err


def test_default_root_is_current_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(["config", "check"], capsys)
    assert (code, out) == (ExitCode.OK, "config OK: no config file found, using defaults\n")


def test_missing_root_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["config", "check", str(tmp_path / "nope")], capsys)
    assert code == ExitCode.USAGE
    assert "is not a directory" in err
