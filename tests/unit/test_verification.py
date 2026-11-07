"""Contract tests for assumptionradar.analysis.verification (ADR-0003).

The verifier is tested with a scripted fake runner; real child processes are
covered by the integration tests of the subprocess runner.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from assumptionradar.analysis.verification import (
    DEFAULT_LIMITATIONS,
    RunOutcome,
    RunResult,
    Scenario,
    ScenarioResult,
    VerificationResult,
    Verifier,
    assess_baseline,
    check_probe,
    classify,
    outcome_from_exit_code,
)
from assumptionradar.core.exceptions import AssumptionRadarError, VerificationError
from assumptionradar.core.models import VerificationStatus as VS

TEST_ID = "tests/test_app.py::test_load"


def run(
    outcome: RunOutcome, probe: Mapping[str, str] | None = None, code: int | None = 0
) -> RunResult:
    return RunResult(outcome=outcome, exit_code=code, duration_s=0.5, output_tail="", probe=probe)


PASS = run(RunOutcome.PASSED)
FAIL = run(RunOutcome.FAILED, code=1)
TZ = Scenario(
    name="tz:+9",
    description="Local timezone UTC+9",
    env_set={"TZ": "JST-9"},
    expect={"utc_offset_seconds": "32400"},
)


# ------------------------------------------------------------------ basic types


class TestOutcomeFromExitCode:
    @pytest.mark.parametrize(
        ("code", "outcome"),
        [
            (0, RunOutcome.PASSED),
            (1, RunOutcome.FAILED),
            (2, RunOutcome.COLLECTION_ERROR),
            (5, RunOutcome.NO_TESTS),
            (3, RunOutcome.ERROR),
            (4, RunOutcome.ERROR),
            (-11, RunOutcome.ERROR),
            (137, RunOutcome.ERROR),
        ],
    )
    def test_mapping(self, code: int, outcome: RunOutcome) -> None:
        assert outcome_from_exit_code(code) is outcome

    def test_values(self) -> None:
        assert [o.value for o in RunOutcome] == [
            "passed",
            "failed",
            "collection_error",
            "no_tests",
            "error",
            "timeout",
        ]


class TestRunResult:
    def test_frozen_and_probe_read_only(self) -> None:
        src = {"b": "1", "a": "2"}
        r = run(RunOutcome.PASSED, probe=src)
        src["c"] = "3"
        assert r.probe is not None
        assert dict(r.probe) == {"a": "2", "b": "1"}
        with pytest.raises(TypeError):
            r.probe["x"] = "y"  # type: ignore[index]
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.exit_code = 3  # type: ignore[misc]

    def test_to_dict(self) -> None:
        r = RunResult(RunOutcome.FAILED, 1, 1.23456, "E   assert 1 == 2", {"cwd": "{isolated_cwd}"})
        assert r.to_dict() == {
            "outcome": "failed",
            "exit_code": 1,
            "duration_s": 1.235,
            "output_tail": "E   assert 1 == 2",
            "probe": {"cwd": "{isolated_cwd}"},
        }

    def test_to_dict_without_probe(self) -> None:
        assert RunResult(RunOutcome.TIMEOUT, None, 60.0).to_dict()["probe"] is None


class TestScenario:
    def test_defaults(self) -> None:
        s = Scenario(name="cwd", description="d")
        assert dict(s.env_set) == {}
        assert s.env_unset == ()
        assert s.isolated_cwd is False
        assert s.isolated_tmp is False
        assert s.child_locale is None
        assert dict(s.expect) == {}
        assert s.unsupported_reason is None
        assert s.supported is True

    def test_unsupported(self) -> None:
        s = Scenario(name="locale:de", description="d", unsupported_reason="locale not installed")
        assert s.supported is False

    @pytest.mark.parametrize(
        "name", ["cwd", "tz:+9", "tz:-5", "env-unset:DATABASE_URL", "locale:de_DE.UTF-8", "tmpdir"]
    )
    def test_valid_names(self, name: str) -> None:
        assert Scenario(name=name, description="d").name == name

    @pytest.mark.parametrize("name", ["", "CWD", "tz :x", "tz:", "1abc", "env unset"])
    def test_invalid_names(self, name: str) -> None:
        with pytest.raises(ValueError, match="name"):
            Scenario(name=name, description="d")

    def test_env_unset_list_coerced_to_tuple(self) -> None:
        s = Scenario(name="env-unset:A", description="d", env_unset=["A"])  # type: ignore[arg-type]
        assert s.env_unset == ("A",)

    @pytest.mark.parametrize("bad", ["", "A=B", "A B"])
    def test_invalid_env_names(self, bad: str) -> None:
        with pytest.raises(ValueError):
            Scenario(name="x", description="d", env_unset=(bad,))
        with pytest.raises(ValueError):
            Scenario(name="x", description="d", env_set={bad: "v"})

    def test_env_set_and_unset_must_be_disjoint(self) -> None:
        with pytest.raises(ValueError, match="both"):
            Scenario(name="x", description="d", env_set={"A": "1"}, env_unset=("A",))

    def test_mappings_are_read_only_copies(self) -> None:
        env = {"TZ": "JST-9"}
        s = Scenario(name="tz:+9", description="d", env_set=env)
        env["TZ"] = "UTC0"
        assert s.env_set["TZ"] == "JST-9"
        with pytest.raises(TypeError):
            s.env_set["X"] = "1"  # type: ignore[index]

    def test_hashable(self) -> None:
        assert hash(TZ) == hash(dataclasses.replace(TZ))

    def test_to_dict(self) -> None:
        s = Scenario(
            name="env-unset:API_URL",
            description="Remove API_URL",
            env_unset=("API_URL",),
            expect={"env:API_URL": "absent"},
        )
        assert s.to_dict() == {
            "name": "env-unset:API_URL",
            "description": "Remove API_URL",
            "env_set": {},
            "env_unset": ["API_URL"],
            "isolated_cwd": False,
            "isolated_tmp": False,
            "child_locale": None,
            "expect": {"env:API_URL": "absent"},
            "unsupported_reason": None,
        }


# --------------------------------------------------------------- pure functions


class TestAssessBaseline:
    def test_all_passed_is_usable(self) -> None:
        assert assess_baseline([PASS, PASS]) == (None, "")

    def test_empty_is_programming_error(self) -> None:
        with pytest.raises(ValueError):
            assess_baseline([])

    def test_flaky(self) -> None:
        status, reason = assess_baseline([PASS, FAIL])
        assert status is VS.INCONCLUSIVE
        assert "flaky" in reason

    def test_failing(self) -> None:
        status, reason = assess_baseline([FAIL, FAIL])
        assert status is VS.INCONCLUSIVE
        assert "failing" in reason

    def test_collection_error_counts_as_failing(self) -> None:
        status, reason = assess_baseline([run(RunOutcome.COLLECTION_ERROR, code=2)])
        assert status is VS.INCONCLUSIVE
        assert "failing" in reason

    def test_not_found(self) -> None:
        status, reason = assess_baseline([run(RunOutcome.NO_TESTS, code=5)])
        assert status is VS.ERROR
        assert "not found" in reason

    def test_error(self) -> None:
        status, reason = assess_baseline([PASS, run(RunOutcome.ERROR, code=3)])
        assert status is VS.ERROR
        assert "error" in reason

    def test_timeout(self) -> None:
        status, reason = assess_baseline([run(RunOutcome.TIMEOUT, code=None)])
        assert status is VS.INCONCLUSIVE
        assert "timed out" in reason


class TestCheckProbe:
    def test_no_expectations(self) -> None:
        assert check_probe({}, None) is None
        assert check_probe({}, {"a": "1"}) is None

    def test_match(self) -> None:
        assert check_probe({"a": "1"}, {"a": "1", "b": "2"}) is None

    def test_missing_probe(self) -> None:
        reason = check_probe({"a": "1"}, None)
        assert reason is not None
        assert "did not report" in reason

    def test_first_mismatch_in_sorted_key_order(self) -> None:
        reason = check_probe({"z": "1", "a": "1"}, {"z": "2", "a": "2"})
        assert reason == "condition did not take effect: a expected '1', got '2'"

    def test_missing_key(self) -> None:
        reason = check_probe({"a": "1"}, {})
        assert reason == "condition did not take effect: a expected '1', got None"


class TestClassify:
    OK_PROBE: ClassVar[dict[str, str]] = {"utc_offset_seconds": "32400"}

    def test_unsupported_scenario(self) -> None:
        s = dataclasses.replace(TZ, unsupported_reason="no tz support")
        assert classify(s, None) == (VS.UNSUPPORTED, "no tz support")

    def test_not_run(self) -> None:
        status, _ = classify(TZ, None)
        assert status is VS.ERROR

    def test_child_error_wins_over_probe(self) -> None:
        status, reason = classify(TZ, run(RunOutcome.ERROR, None, code=3))
        assert status is VS.ERROR
        assert "exit code 3" in reason

    def test_condition_not_applied_is_unsupported_not_no_difference(self) -> None:
        # Spike 0001: TZ=Asia/Tokyo silently became UTC on Windows.
        status, reason = classify(TZ, run(RunOutcome.PASSED, {"utc_offset_seconds": "0"}))
        assert status is VS.UNSUPPORTED
        assert "did not take effect" in reason

    def test_condition_not_applied_and_failed_is_not_regression(self) -> None:
        status, _ = classify(TZ, run(RunOutcome.FAILED, {"utc_offset_seconds": "0"}, code=1))
        assert status is VS.UNSUPPORTED

    def test_missing_probe_is_error(self) -> None:
        status, _ = classify(TZ, run(RunOutcome.PASSED, None))
        assert status is VS.ERROR

    def test_passed_is_no_difference(self) -> None:
        assert classify(TZ, run(RunOutcome.PASSED, self.OK_PROBE))[0] is VS.NO_DIFFERENCE

    def test_failed_is_regression(self) -> None:
        status, reason = classify(TZ, run(RunOutcome.FAILED, self.OK_PROBE, code=1))
        assert status is VS.REGRESSION
        assert "failed" in reason

    def test_collection_error_is_regression(self) -> None:
        status, reason = classify(TZ, run(RunOutcome.COLLECTION_ERROR, self.OK_PROBE, code=2))
        assert status is VS.REGRESSION
        assert "collect" in reason

    def test_timeout_is_inconclusive(self) -> None:
        status, _ = classify(TZ, run(RunOutcome.TIMEOUT, self.OK_PROBE, code=None))
        assert status is VS.INCONCLUSIVE

    def test_no_tests_is_error(self) -> None:
        assert classify(TZ, run(RunOutcome.NO_TESTS, self.OK_PROBE, code=5))[0] is VS.ERROR

    def test_scenario_without_expectations_needs_no_probe(self) -> None:
        s = Scenario(name="plain", description="d")
        assert classify(s, run(RunOutcome.FAILED, None, code=1))[0] is VS.REGRESSION


# ------------------------------------------------------------------- VerificationResult


def sr(status: VS, name: str = "x") -> ScenarioResult:
    return ScenarioResult(scenario=name, status=status, reason="r")


class TestVerificationResult:
    @pytest.mark.parametrize(
        ("statuses", "overall"),
        [
            ([VS.NO_DIFFERENCE, VS.REGRESSION, VS.ERROR], VS.REGRESSION),
            ([VS.NO_DIFFERENCE, VS.ERROR, VS.INCONCLUSIVE], VS.ERROR),
            ([VS.NO_DIFFERENCE, VS.INCONCLUSIVE, VS.UNSUPPORTED], VS.INCONCLUSIVE),
            ([VS.NO_DIFFERENCE, VS.UNSUPPORTED], VS.NO_DIFFERENCE),
            ([VS.UNSUPPORTED, VS.UNSUPPORTED], VS.UNSUPPORTED),
            ([], VS.INCONCLUSIVE),
        ],
    )
    def test_overall_status(self, statuses: list[VS], overall: VS) -> None:
        res = VerificationResult(
            test_id=TEST_ID,
            baseline=(PASS,),
            scenarios=tuple(sr(s, f"s{i}") for i, s in enumerate(statuses)),
        )
        assert res.status is overall

    def test_regressions(self) -> None:
        res = VerificationResult(
            TEST_ID,
            (PASS,),
            (sr(VS.REGRESSION, "a"), sr(VS.NO_DIFFERENCE, "b"), sr(VS.REGRESSION, "c")),
        )
        assert [r.scenario for r in res.regressions] == ["a", "c"]

    def test_default_limitations(self) -> None:
        res = VerificationResult(TEST_ID, (PASS,), ())
        assert res.limitations == DEFAULT_LIMITATIONS
        joined = " ".join(DEFAULT_LIMITATIONS).lower()
        assert "not a sandbox" in joined
        assert "does not prove" in joined

    def test_to_dict_is_json_ready(self) -> None:
        res = VerificationResult(
            TEST_ID,
            (PASS,),
            (
                ScenarioResult(
                    "tz:+9", VS.REGRESSION, "boom", run(RunOutcome.FAILED, {"a": "1"}, 1)
                ),
            ),
        )
        d = res.to_dict()
        assert list(d) == ["test_id", "status", "baseline", "scenarios", "limitations"]
        assert d["status"] == "regression"
        assert d["scenarios"] == [
            {
                "scenario": "tz:+9",
                "status": "regression",
                "reason": "boom",
                "run": {
                    "outcome": "failed",
                    "exit_code": 1,
                    "duration_s": 0.5,
                    "output_tail": "",
                    "probe": {"a": "1"},
                },
            }
        ]
        json.dumps(d)

    def test_scenario_result_without_run(self) -> None:
        assert sr(VS.UNSUPPORTED).to_dict()["run"] is None


# --------------------------------------------------------------------- Verifier


class FakeRunner:
    """Scripted runner: baseline results are consumed in order, scenarios by name."""

    def __init__(
        self, baseline: Sequence[RunResult], by_scenario: Mapping[str, RunResult] | None = None
    ) -> None:
        self.baseline = list(baseline)
        self.by_scenario = dict(by_scenario or {})
        self.calls: list[tuple[str, Path, str | None]] = []

    def __call__(self, test_id: str, project_root: Path, scenario: Scenario | None) -> RunResult:
        self.calls.append((test_id, project_root, scenario.name if scenario else None))
        if scenario is None:
            return self.baseline.pop(0)
        return self.by_scenario[scenario.name]


CWD = Scenario(
    name="cwd", description="empty cwd", isolated_cwd=True, expect={"cwd": "{isolated_cwd}"}
)


class TestVerifier:
    def test_exceptions_hierarchy(self) -> None:
        assert issubclass(VerificationError, AssumptionRadarError)

    def test_project_root_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(VerificationError, match="project root"):
            Verifier(tmp_path / "missing", runner=FakeRunner([]))

    def test_project_root_must_be_directory(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(VerificationError):
            Verifier(f, runner=FakeRunner([]))

    def test_baseline_runs_must_be_positive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            Verifier(tmp_path, runner=FakeRunner([]), baseline_runs=0)

    @pytest.mark.parametrize("bad", ["", "   ", "a::b\nc"])
    def test_invalid_test_id(self, tmp_path: Path, bad: str) -> None:
        v = Verifier(tmp_path, runner=FakeRunner([PASS, PASS]))
        with pytest.raises(ValueError):
            v.verify(bad, [TZ])

    def test_duplicate_scenarios_rejected(self, tmp_path: Path) -> None:
        v = Verifier(tmp_path, runner=FakeRunner([PASS, PASS]))
        with pytest.raises(ValueError, match="duplicate"):
            v.verify(TEST_ID, [TZ, TZ])

    def test_happy_path_call_order_and_results(self, tmp_path: Path) -> None:
        runner = FakeRunner(
            [PASS, PASS],
            {
                "tz:+9": run(RunOutcome.FAILED, {"utc_offset_seconds": "32400"}, 1),
                "cwd": run(RunOutcome.PASSED, {"cwd": "{isolated_cwd}"}),
            },
        )
        res = Verifier(tmp_path, runner=runner).verify(TEST_ID, [TZ, CWD])
        root = tmp_path.resolve()
        assert runner.calls == [
            (TEST_ID, root, None),
            (TEST_ID, root, None),
            (TEST_ID, root, "tz:+9"),
            (TEST_ID, root, "cwd"),
        ]
        assert res.test_id == TEST_ID
        assert res.baseline == (PASS, PASS)
        assert [(r.scenario, r.status) for r in res.scenarios] == [
            ("tz:+9", VS.REGRESSION),
            ("cwd", VS.NO_DIFFERENCE),
        ]
        assert res.scenarios[0].run is not None
        assert res.status is VS.REGRESSION

    def test_baseline_runs_count(self, tmp_path: Path) -> None:
        runner = FakeRunner(
            [PASS, PASS, PASS], {"cwd": run(RunOutcome.PASSED, {"cwd": "{isolated_cwd}"})}
        )
        Verifier(tmp_path, runner=runner, baseline_runs=3).verify(TEST_ID, [CWD])
        assert [c[2] for c in runner.calls] == [None, None, None, "cwd"]

    def test_bad_baseline_skips_scenarios(self, tmp_path: Path) -> None:
        runner = FakeRunner([PASS, FAIL])
        res = Verifier(tmp_path, runner=runner).verify(TEST_ID, [TZ, CWD])
        assert [c[2] for c in runner.calls] == [None, None]
        assert [(r.scenario, r.status, r.run) for r in res.scenarios] == [
            ("tz:+9", VS.INCONCLUSIVE, None),
            ("cwd", VS.INCONCLUSIVE, None),
        ]
        assert all("flaky" in r.reason for r in res.scenarios)

    def test_unsupported_scenario_is_not_run(self, tmp_path: Path) -> None:
        s = Scenario(name="locale:de", description="d", unsupported_reason="locale not installed")
        runner = FakeRunner([PASS, PASS])
        res = Verifier(tmp_path, runner=runner).verify(TEST_ID, [s])
        assert [c[2] for c in runner.calls] == [None, None]
        assert res.scenarios[0].status is VS.UNSUPPORTED
        assert res.scenarios[0].reason == "locale not installed"

    def test_runner_exception_becomes_error_result(self, tmp_path: Path) -> None:
        class Boom(FakeRunner):
            def __call__(
                self, test_id: str, project_root: Path, scenario: Scenario | None
            ) -> RunResult:
                if scenario is not None:
                    raise OSError("cannot spawn")
                return super().__call__(test_id, project_root, scenario)

        res = Verifier(tmp_path, runner=Boom([PASS, PASS])).verify(TEST_ID, [TZ])
        assert res.scenarios[0].status is VS.ERROR
        assert "cannot spawn" in res.scenarios[0].reason

    def test_baseline_runner_exception_raises_verification_error(self, tmp_path: Path) -> None:
        class Boom(FakeRunner):
            def __call__(
                self, test_id: str, project_root: Path, scenario: Scenario | None
            ) -> RunResult:
                raise OSError("no python")

        with pytest.raises(VerificationError, match="no python"):
            Verifier(tmp_path, runner=Boom([])).verify(TEST_ID, [TZ])

    def test_parent_process_state_untouched(self, tmp_path: Path) -> None:
        env_before = dict(os.environ)
        cwd_before = Path.cwd()
        runner = FakeRunner(
            [PASS, PASS], {"tz:+9": run(RunOutcome.PASSED, {"utc_offset_seconds": "32400"})}
        )
        Verifier(tmp_path, runner=runner).verify(TEST_ID, [TZ])
        assert dict(os.environ) == env_before
        assert Path.cwd() == cwd_before
