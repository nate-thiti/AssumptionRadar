"""End-to-end test on the fixture projects (P1-15).

Runs the real plugin in a separate pytest process on ``tests/fixtures/positive``
and ``tests/fixtures/negative`` and compares the report with the exact expected
result: every planted assumption is reported, and nothing else is. Then the
saved report is shown through the CLI to check its exit code.
"""

from __future__ import annotations

import json
import locale
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from assumptionradar.cli import ExitCode
from assumptionradar.reporting.json_report import load_report_schema

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TIMESTAMP = 1_700_000_000
ENV_NAME = "AR_FIXTURE_API_URL"
SPEC = f"cwd,tz,encoding,env-unset:{ENV_NAME}"
FAMILIES = ("cwd", "tz", "encoding", "env-unset")

ND = "no_difference"
REG = "regression"


def expected_scenarios(**regressions: str) -> dict[str, set[str]]:
    """Every family no_difference except the ones given (keyword uses _ for -)."""
    result = {family: {ND} for family in FAMILIES}
    for family, status in regressions.items():
        result[family.replace("_", "-")] = {status}
    return result


POSITIVE: dict[str, tuple[str, dict[str, set[str]]]] = {
    "test_assumptions.py::test_config": (REG, expected_scenarios(cwd=REG)),
    "test_assumptions.py::test_local_time": (REG, expected_scenarios(tz=REG)),
    "test_assumptions.py::test_env": (REG, expected_scenarios(env_unset=REG)),
    "test_assumptions.py::test_git": (ND, expected_scenarios()),
    "test_assumptions.py::test_encoding": (REG, expected_scenarios(encoding=REG)),
}

POSITIVE_FLAGGED = {
    ("test_assumptions.py::test_config", "file_open", "config.json"),
    ("test_assumptions.py::test_git", "subprocess", "git"),
}

NEGATIVE: dict[str, tuple[str, dict[str, set[str]]]] = {
    f"test_portable.py::{name}": (ND, expected_scenarios())
    for name in (
        "test_config",
        "test_explicit_time_zone",
        "test_env",
        "test_subprocess_absolute_path",
        "test_encoding",
    )
}


def host_char() -> str:
    """A non-ASCII character the host's default encoding can represent."""
    host = locale.getpreferredencoding(False)
    for char in ("é", "ก", "ß", "ж"):
        try:
            char.encode(host)
        except UnicodeEncodeError:
            continue
        return char
    pytest.skip(f"no test character is encodable in {host}")


def prepare(name: str, tmp_path: Path) -> Path:
    """Copy a fixture project and write the files recorded 'on the author's machine'."""
    project = tmp_path / name
    shutil.copytree(FIXTURES / name, project)
    if name == "positive":
        local = datetime.fromtimestamp(TIMESTAMP).strftime("%Y-%m-%d %H:%M")  # noqa: DTZ006
        (project / "expected_time.txt").write_text(local + "\n", encoding="utf-8")
        char = host_char()
        (project / "notes.txt").write_text(char, encoding=locale.getpreferredencoding(False))
        (project / "notes_char.txt").write_text(char, encoding="utf-8")
    return project


def run_project(project: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    env = {**os.environ, ENV_NAME: "https://api.example.test"}
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--assumption-observe",
            f"--assumption-verify={SPEC}",
            "--assumption-report=report.json",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    report: dict[str, Any] = json.loads((project / "report.json").read_text(encoding="utf-8"))
    jsonschema.validate(report, load_report_schema())
    return result, report


def verdicts(report: dict[str, Any]) -> dict[str, tuple[str, dict[str, set[str]]]]:
    found: dict[str, tuple[str, dict[str, set[str]]]] = {}
    for v in report["verifications"]:
        by_family: dict[str, set[str]] = {}
        for s in v["scenarios"]:
            by_family.setdefault(s["scenario"].split(":", 1)[0], set()).add(s["status"])
        found[v["test_id"]] = (v["status"], by_family)
    return found


def flagged(report: dict[str, Any]) -> set[tuple[str | None, str, str]]:
    """Events the terminal report marks with '!' (likely assumptions)."""
    out: set[tuple[str | None, str, str]] = set()
    for e in report["observation"]["events"]:
        a = e["attributes"]
        if (
            a.get("relative") == "true"
            or a.get("path_lookup") == "true"
            or (e["kind"] == "env_read" and a.get("present") == "false")
        ):
            out.add((e["test_id"], e["kind"], e["target"]))
    return out


def show(project: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "assumptionradar", "report", "show", "report.json"],
        cwd=project,
        capture_output=True,
        check=False,
    ).returncode


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_positive_project_reports_every_planted_assumption(tmp_path: Path) -> None:
    project = prepare("positive", tmp_path)
    result, report = run_project(project)
    assert result.returncode == 0, result.stdout + result.stderr  # never fails the run
    assert verdicts(report) == POSITIVE, result.stdout
    assert flagged(report) == POSITIVE_FLAGGED
    assert report["verification_skipped"] == []
    assert report["observation"]["errors"] == []
    assert show(project) == ExitCode.FINDINGS


def test_negative_project_reports_nothing(tmp_path: Path) -> None:
    project = prepare("negative", tmp_path)
    result, report = run_project(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert verdicts(report) == NEGATIVE, result.stdout
    assert flagged(report) == set()
    assert report["verification_skipped"] == []
    assert report["observation"]["errors"] == []
    assert show(project) == ExitCode.OK
