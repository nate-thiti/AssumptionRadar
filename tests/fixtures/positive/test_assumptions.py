"""Tests that pass on the author's machine but hide environment assumptions.

Each test is a known positive for AssumptionRadar. The end-to-end test in
``tests/e2e/test_fixture_projects.py`` copies this project to a temporary
directory, generates ``expected_time.txt`` and ``notes.txt`` there, and checks
that exactly these assumptions are reported. Do not "fix" this code.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
TIMESTAMP = 1_700_000_000


def test_config() -> None:
    # Assumption: pytest is started from the project directory.
    with open("config.json") as fh:
        assert json.load(fh) == {"name": "demo"}


def test_local_time() -> None:
    # Assumption: local time zone equals the one the snapshot was recorded in.
    expected = (HERE / "expected_time.txt").read_text(encoding="utf-8").strip()
    assert datetime.fromtimestamp(TIMESTAMP).strftime("%Y-%m-%d %H:%M") == expected


def test_env() -> None:
    # Assumption: the variable is always set.
    assert os.environ["AR_FIXTURE_API_URL"].startswith("https://")


def test_git() -> None:
    # Assumption: a program called "git" is found on PATH.
    result = subprocess.run(["git", "--version"], capture_output=True, check=True)
    assert result.stdout.startswith(b"git version")


def test_encoding() -> None:
    # Assumption: the default text encoding is the one the file was written in.
    with open(HERE / "notes.txt") as fh:
        assert fh.read() == (HERE / "notes_char.txt").read_text(encoding="utf-8")
