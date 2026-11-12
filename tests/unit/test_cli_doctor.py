"""Tests for ``assumptionradar doctor`` (P2-11)."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from pathlib import Path

import pytest

import assumptionradar
from assumptionradar.cli import ExitCode, main

KEYS = [
    "assumptionradar",
    "python",
    "platform",
    "root",
    "config_file",
    "config_error",
    "pyproject",
    "active_rules",
    "cache",
    "pytest",
]


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    return root


def test_doctor_json_defaults(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path)
    code, out, err = run(["doctor", str(root), "--format", "json"], capsys)
    assert (code, err) == (ExitCode.OK, "")
    data = json.loads(out)
    assert list(data) == KEYS
    assert data["assumptionradar"] == assumptionradar.__version__
    assert data["python"] == {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
    }
    assert data["platform"] == sys.platform
    assert data["root"] == str(root)
    assert data["config_file"] is None
    assert data["config_error"] is None
    assert data["pyproject"] is False
    assert data["active_rules"] == ["AR-CWD-001", "AR-ENV-001", "AR-EXE-001", "AR-PLAT-001"]
    assert data["cache"] == {
        "enabled": True,
        "directory": str(root / ".assumptionradar_cache"),
    }
    assert data["pytest"] == importlib.metadata.version("pytest")


def test_doctor_json_with_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path)
    path = root / "pyproject.toml"
    path.write_text(
        "[project]\nname = 'x'\n[tool.assumptionradar]\ncache = false\n"
        "ignore-rules = ['AR-PLAT-001']\n",
        encoding="utf-8",
    )
    code, out, _ = run(["doctor", str(root), "--format", "json"], capsys)
    assert code == ExitCode.OK
    data = json.loads(out)
    assert data["config_file"] == str(path)
    assert data["pyproject"] is True
    assert data["active_rules"] == ["AR-CWD-001", "AR-ENV-001", "AR-EXE-001", "AR-IMP-001"]
    assert data["cache"]["enabled"] is False


def test_doctor_reports_a_bad_config_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)
    (root / "assumptionradar.toml").write_text('fail-on = "loud"\n', encoding="utf-8")
    code, out, _ = run(["doctor", str(root), "--format", "json"], capsys)
    assert code == ExitCode.USAGE
    data = json.loads(out)
    assert data["config_file"] is None
    assert "'fail-on' must be one of" in data["config_error"]
    assert data["active_rules"] == ["AR-CWD-001", "AR-ENV-001", "AR-EXE-001", "AR-PLAT-001"]


def test_doctor_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path)
    code, out, _ = run(["doctor", str(root)], capsys)
    assert code == ExitCode.OK
    lines = out.splitlines()
    labels = [line[:17] for line in lines]
    assert labels == [
        "assumptionradar  ",
        "python           ",
        "platform         ",
        "project root     ",
        "config           ",
        "pyproject.toml   ",
        "active rules     ",
        "cache            ",
        "pytest           ",
    ]
    assert lines[0] == f"assumptionradar  {assumptionradar.__version__}"
    assert lines[3] == f"project root     {root}"
    assert lines[4] == "config           none found, using defaults"
    assert lines[5] == "pyproject.toml   not found (AR-IMP-001 needs one)"
    assert lines[6] == "active rules     AR-CWD-001, AR-ENV-001, AR-EXE-001, AR-PLAT-001"
    assert lines[7] == f"cache            {root / '.assumptionradar_cache'}"
    assert lines[8] == f"pytest           {importlib.metadata.version('pytest')}"


def test_doctor_text_with_config_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path)
    (root / "assumptionradar.toml").write_text("cache = 1\n", encoding="utf-8")
    code, out, _ = run(["doctor", str(root)], capsys)
    assert code == ExitCode.USAGE
    config_line = out.splitlines()[4]
    assert config_line.startswith("config           ERROR: ")
    assert "'cache' must be true or false" in config_line

    (root / "assumptionradar.toml").write_text("cache = false\n", encoding="utf-8")
    code, out, _ = run(["doctor", str(root)], capsys)
    assert code == ExitCode.OK
    lines = out.splitlines()
    assert lines[4] == f"config           {root / 'assumptionradar.toml'}"
    assert lines[7] == "cache            disabled"


def test_doctor_without_pytest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    root = repo(tmp_path)
    _, out, _ = run(["doctor", str(root), "--format", "json"], capsys)
    assert json.loads(out)["pytest"] is None
    _, out, _ = run(["doctor", str(root)], capsys)
    assert out.splitlines()[8] == "pytest           not installed"


def test_doctor_default_root_and_missing_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    monkeypatch.chdir(root)
    code, out, _ = run(["doctor", "--format", "json"], capsys)
    assert code == ExitCode.OK
    assert json.loads(out)["root"] == "."
    code, _, err = run(["doctor", str(tmp_path / "nope")], capsys)
    assert code == ExitCode.USAGE
    assert "is not a directory" in err
