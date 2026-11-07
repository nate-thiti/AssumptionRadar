"""Portable versions of the positive fixture tests: nothing should be reported.

Each test does the same job as its counterpart in ``tests/fixtures/positive``
without the hidden assumption. The end-to-end test checks that AssumptionRadar
reports no regression and flags no event here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

HERE = Path(__file__).parent
TIMESTAMP = 1_700_000_000


def test_config() -> None:
    with (HERE / "config.json").open(encoding="utf-8") as fh:
        assert json.load(fh) == {"name": "demo"}


def test_explicit_time_zone() -> None:
    assert datetime.fromtimestamp(TIMESTAMP, tz=UTC).strftime("%Y-%m-%d %H:%M") == (
        "2023-11-14 22:13"
    )


def test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AR_FIXTURE_API_URL", "https://example.test")
    assert os.environ["AR_FIXTURE_API_URL"].startswith("https://")


def test_subprocess_absolute_path() -> None:
    result = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, check=True)
    assert result.stdout.strip() == b"ok"


def test_encoding() -> None:
    text = "café ก"
    path = HERE / "utf8.txt"
    path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text
