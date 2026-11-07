"""Tests for the version and help output of the assumptionradar CLI."""

import importlib.metadata
import subprocess
import sys

import pytest

from assumptionradar import __version__
from assumptionradar.cli import main


def test_version_matches_package_metadata() -> None:
    assert __version__ == importlib.metadata.version("assumptionradar")


def test_module_version_flag_prints_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "assumptionradar", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"assumptionradar {__version__}"


def test_module_help_flag_prints_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "assumptionradar", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "assumptionradar" in result.stdout


def test_main_without_args_prints_help_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "assumptionradar" in capsys.readouterr().out
