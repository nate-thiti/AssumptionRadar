"""Contract tests for the pytest plugin (P1-11) using pytest's ``pytester``.

Observation runs in-process (``runpytest``); verification spawns child pytest
processes, so those tests use ``runpytest_subprocess`` for realism.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from assumptionradar.analysis import runtime
from assumptionradar.reporting.json_report import load_report_schema

RELATIVE_READ = """
def test_reads():
    with open("data.txt", encoding="utf-8") as fh:
        assert fh.read() == "ok"
"""

FILE_RELATIVE_READ = """
from pathlib import Path

def test_ok():
    assert (Path(__file__).parent / "data.txt").read_text(encoding="utf-8") == "ok"
"""


VALIDATOR = jsonschema.Draft202012Validator(load_report_schema())


def load(path: Path) -> dict[str, Any]:
    """Read a report written by a real run and check it against the published schema."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    problems = [f"{list(e.absolute_path)}: {e.message}" for e in VALIDATOR.iter_errors(data)]
    assert problems == [], problems
    return data


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makefile(".txt", data="ok")
    return pytester


# ------------------------------------------------------------------- options


def test_help_lists_options(pytester: pytest.Pytester) -> None:
    out = pytester.runpytest("--help").stdout.str()
    for option in (
        "--assumption-observe",
        "--assumption-env-reads",
        "--assumption-verify",
        "--assumption-verify-max",
        "--assumption-timeout",
        "--assumption-report",
        "--assumption-strict",
    ):
        assert option in out


def test_inactive_by_default(project: pytest.Pytester) -> None:
    project.makepyfile(test_obs=RELATIVE_READ)
    result = project.runpytest()
    result.assert_outcomes(passed=1)
    # The installed plugin is listed in pytest's header; it must add no section.
    assert re.search(r"=+ assumptionradar =+", result.stdout.str()) is None
    assert runtime._ACTIVE is None


# --------------------------------------------------------------- observation


class TestObserve:
    def test_terminal_summary(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_obs=RELATIVE_READ)
        result = project.runpytest("--assumption-observe")
        result.assert_outcomes(passed=1)
        assert result.ret == 0
        out = result.stdout.str()
        assert "assumptionradar" in out
        assert "file_open" in out
        assert "data.txt" in out
        assert "test_obs.py::test_reads" in out

    def test_observer_released_after_session(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_obs=RELATIVE_READ)
        project.runpytest("--assumption-observe")
        assert runtime._ACTIVE is None

    def test_json_report(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_obs=RELATIVE_READ)
        result = project.runpytest("--assumption-observe", "--assumption-report=out.json")
        assert result.ret == 0
        report = load(project.path / "out.json")
        assert report["schema_version"] == "1"
        assert report["tool"]["name"] == "assumptionradar"
        assert isinstance(report["tool"]["version"], str)
        assert set(report["platform"]) == {"system", "python"}
        assert report["verifications"] == []
        events = report["observation"]["events"]
        reads = [e for e in events if e["kind"] == "file_open" and e["target"] == "data.txt"]
        assert len(reads) == 1
        assert reads[0]["test_id"] == "test_obs.py::test_reads"
        assert reads[0]["attributes"]["relative"] == "true"
        assert reads[0]["location"]["path"] == "test_obs.py"

    def test_collection_time_event_has_no_test_id(self, project: pytest.Pytester) -> None:
        project.makepyfile(
            test_mod="""
            CONFIG = open("data.txt", encoding="utf-8").read()

            def test_it():
                assert CONFIG == "ok"
            """
        )
        project.runpytest("--assumption-observe", "--assumption-report=out.json")
        events = load(project.path / "out.json")["observation"]["events"]
        reads = [e for e in events if e["target"] == "data.txt"]
        assert len(reads) == 1
        assert reads[0]["test_id"] is None

    def test_fixture_setup_is_attributed_to_test(self, project: pytest.Pytester) -> None:
        project.makeconftest(
            """
            import pytest

            @pytest.fixture
            def config_text():
                with open("data.txt", encoding="utf-8") as fh:
                    return fh.read()
            """
        )
        project.makepyfile(test_fx="def test_it(config_text):\n    assert config_text == 'ok'\n")
        project.runpytest("--assumption-observe", "--assumption-report=out.json")
        events = load(project.path / "out.json")["observation"]["events"]
        reads = [e for e in events if e["target"] == "data.txt"]
        assert [e["test_id"] for e in reads] == ["test_fx.py::test_it"]
        assert reads[0]["location"]["path"] == "conftest.py"

    def test_env_reads_opt_in(
        self, project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AR_PLUGIN_MISSING", raising=False)
        project.makepyfile(
            test_env="import os\n\ndef test_it():\n    assert os.environ.get('AR_PLUGIN_MISSING') is None\n"
        )
        project.runpytest("--assumption-observe", "--assumption-report=a.json")
        project.runpytest(
            "--assumption-observe", "--assumption-env-reads", "--assumption-report=b.json"
        )
        a = [
            e
            for e in load(project.path / "a.json")["observation"]["events"]
            if e["kind"] == "env_read"
        ]
        b = [
            e
            for e in load(project.path / "b.json")["observation"]["events"]
            if e["kind"] == "env_read"
        ]
        assert a == []
        assert [(e["target"], e["attributes"]["present"]) for e in b] == [
            ("AR_PLUGIN_MISSING", "false")
        ]

    def test_env_reads_requires_observe(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_x="def test_it():\n    pass\n")
        result = project.runpytest("--assumption-env-reads")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        assert "--assumption-observe" in result.stderr.str()

    def test_observation_never_fails_tests(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_obs=RELATIVE_READ)
        result = project.runpytest("--assumption-observe", "--assumption-strict")
        assert result.ret == 0


# -------------------------------------------------------------- verification


class TestVerify:
    def test_regression_and_no_difference(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_rel=RELATIVE_READ, test_ok=FILE_RELATIVE_READ)
        result = project.runpytest_subprocess(
            "--assumption-verify=cwd", "--assumption-report=out.json"
        )
        assert result.ret == 0  # not strict: verification never changes the exit code
        out = result.stdout.str()
        assert "REGRESSION    test_rel.py::test_reads" in out
        assert "cwd: regression (reproduced)" in out
        assert "fix: build paths from Path(__file__).parent" in out
        assert "NO_DIFFERENCE test_ok.py::test_ok" in out
        # The progress message starts on its own line, not after pytest's "[100%]".
        assert any(line.startswith("assumptionradar: verifying") for line in out.splitlines())
        report = load(project.path / "out.json")
        assert report["observation"] is None
        by_id = {v["test_id"]: v for v in report["verifications"]}
        assert by_id["test_rel.py::test_reads"]["status"] == "regression"
        assert by_id["test_ok.py::test_ok"]["status"] == "no_difference"
        assert [s["scenario"] for s in by_id["test_rel.py::test_reads"]["scenarios"]] == ["cwd"]
        assert report["verification_skipped"] == []

    def test_observe_and_verify_together(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_rel=RELATIVE_READ)
        result = project.runpytest_subprocess(
            "--assumption-observe", "--assumption-verify=cwd,tz", "--assumption-report=out.json"
        )
        assert result.ret == 0
        report = load(project.path / "out.json")  # validates against the schema
        assert report["observation"] is not None
        assert [v["test_id"] for v in report["verifications"]] == ["test_rel.py::test_reads"]
        runs = report["verifications"][0]["baseline"]
        assert all(run["probe"] for run in runs)

    def test_strict_fails_on_regression(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_rel=RELATIVE_READ)
        result = project.runpytest_subprocess("--assumption-verify=cwd", "--assumption-strict")
        assert result.ret == pytest.ExitCode.TESTS_FAILED

    def test_strict_passes_without_regression(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_ok=FILE_RELATIVE_READ)
        result = project.runpytest_subprocess("--assumption-verify=cwd", "--assumption-strict")
        assert result.ret == 0

    def test_failed_tests_are_not_verified(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_mix=FILE_RELATIVE_READ + "\n\ndef test_bad():\n    assert False\n")
        result = project.runpytest_subprocess(
            "--assumption-verify=cwd", "--assumption-report=out.json"
        )
        assert result.ret == pytest.ExitCode.TESTS_FAILED
        report = load(project.path / "out.json")
        assert [v["test_id"] for v in report["verifications"]] == ["test_mix.py::test_ok"]
        assert report["verification_skipped"] == [
            {"test_id": "test_mix.py::test_bad", "reason": "did not pass in this session"}
        ]

    def test_verify_max(self, project: pytest.Pytester) -> None:
        project.makepyfile(
            test_many="def test_a():\n    pass\n\ndef test_b():\n    pass\n\ndef test_c():\n    pass\n"
        )
        project.runpytest_subprocess(
            "--assumption-verify=cwd", "--assumption-verify-max=1", "--assumption-report=out.json"
        )
        report = load(project.path / "out.json")
        assert [v["test_id"] for v in report["verifications"]] == ["test_many.py::test_a"]
        assert report["verification_skipped"] == [
            {"test_id": "test_many.py::test_b", "reason": "verification limit reached"},
            {"test_id": "test_many.py::test_c", "reason": "verification limit reached"},
        ]

    def test_invalid_spec_is_usage_error(self, project: pytest.Pytester) -> None:
        project.makepyfile(test_x="def test_it():\n    pass\n")
        result = project.runpytest("--assumption-verify=nope")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        assert "unknown scenario" in result.stderr.str()

    @pytest.mark.parametrize("bad", ["0", "-1"])
    def test_invalid_numbers_are_usage_errors(self, project: pytest.Pytester, bad: str) -> None:
        project.makepyfile(test_x="def test_it():\n    pass\n")
        r1 = project.runpytest("--assumption-verify=cwd", f"--assumption-verify-max={bad}")
        r2 = project.runpytest("--assumption-verify=cwd", f"--assumption-timeout={bad}")
        assert r1.ret == pytest.ExitCode.USAGE_ERROR
        assert r2.ret == pytest.ExitCode.USAGE_ERROR

    def test_children_do_not_recurse(
        self, project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Options from PYTEST_ADDOPTS reach the child processes too; the plugin must
        # be disabled there, or every child would start its own verification.
        monkeypatch.setenv("PYTEST_ADDOPTS", "--assumption-verify=cwd")
        project.makepyfile(test_ok=FILE_RELATIVE_READ)
        result = project.runpytest_subprocess("--assumption-report=out.json", timeout=300)
        assert result.ret == 0
        report = load(project.path / "out.json")
        assert [v["status"] for v in report["verifications"]] == ["no_difference"]


# ------------------------------------------------------------- robustness


def test_report_write_failure_is_reported_not_fatal(project: pytest.Pytester) -> None:
    project.makepyfile(test_obs=RELATIVE_READ)
    (project.path / "a_directory").mkdir()
    result = project.runpytest("--assumption-observe", "--assumption-report=a_directory")
    assert result.ret == 0
    assert "could not write report" in result.stdout.str() + result.stderr.str()


def test_report_path_is_relative_to_invocation_dir(project: pytest.Pytester) -> None:
    project.makepyfile(test_obs=RELATIVE_READ)
    (project.path / "reports").mkdir()
    project.runpytest("--assumption-observe", "--assumption-report=reports/r.json")
    assert (project.path / "reports" / "r.json").is_file()


def test_xdist_is_rejected(project: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    project.makepyfile(test_x="def test_it():\n    pass\n")
    result = project.runpytest("-n", "2", "--assumption-observe")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "pytest-xdist" in result.stderr.str()


def test_xdist_without_assumption_options_is_unaffected(project: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    project.makepyfile(test_x="def test_it():\n    pass\n")
    result = project.runpytest("-n", "2")
    assert result.ret == 0
