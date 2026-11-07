"""Pure verification logic for re-running a test under controlled conditions (ADR-0003).

Nothing in this module starts a subprocess. The child process is supplied by
the caller through the :class:`Runner` protocol, which keeps the decision
logic deterministic and testable with scripted runners.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from assumptionradar.core.exceptions import VerificationError
from assumptionradar.core.models import VerificationStatus

_SCENARIO_NAME_RE = re.compile(r"[a-z][a-z0-9_-]*(?::\S+)?")

DEFAULT_LIMITATIONS: tuple[str, ...] = (
    "A child process is not a sandbox: the test runs with your user's privileges "
    "and can affect the host.",
    "'no_difference' means this scenario did not change the test outcome; it does not "
    "prove the test is independent of the environment.",
    "Only the selected test was re-run; interactions with other tests are not covered.",
)


class RunOutcome(StrEnum):
    """Classified result of a single child test run."""

    PASSED = "passed"
    FAILED = "failed"
    COLLECTION_ERROR = "collection_error"
    NO_TESTS = "no_tests"
    ERROR = "error"
    TIMEOUT = "timeout"


_EXIT_CODE_OUTCOMES: Mapping[int, RunOutcome] = MappingProxyType(
    {
        0: RunOutcome.PASSED,
        1: RunOutcome.FAILED,
        2: RunOutcome.COLLECTION_ERROR,
        5: RunOutcome.NO_TESTS,
    }
)


def outcome_from_exit_code(code: int) -> RunOutcome:
    """Map a pytest-style exit code to a :class:`RunOutcome`.

    Codes other than 0, 1, 2 and 5 are reported as ``RunOutcome.ERROR``.
    """
    return _EXIT_CODE_OUTCOMES.get(code, RunOutcome.ERROR)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome of one child test run.

    Attributes:
        outcome: Classified outcome of the run.
        exit_code: Child exit code, or None when the run timed out or never exited.
        duration_s: Wall-clock duration in seconds.
        output_tail: Last lines of child output, for display.
        probe: Values the child reported about its own environment, if any.
    """

    outcome: RunOutcome
    exit_code: int | None
    duration_s: float
    output_tail: str = ""
    probe: Mapping[str, str] | None = field(default=None, hash=False)

    def __post_init__(self) -> None:
        """Copy the probe into a sorted, read-only mapping."""
        if self.probe is not None:
            object.__setattr__(
                self,
                "probe",
                MappingProxyType(dict(sorted(self.probe.items()))),
            )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "outcome": str(self.outcome),
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "output_tail": self.output_tail,
            "probe": dict(self.probe) if self.probe is not None else None,
        }


def _check_env_name(name: str) -> None:
    """Raise ValueError unless ``name`` is a usable environment variable name."""
    if not name or "=" in name or any(ch.isspace() for ch in name):
        raise ValueError(f"invalid environment variable name {name!r}")


@dataclass(frozen=True, slots=True)
class Scenario:
    """A controlled condition under which the selected test is re-run.

    Attributes:
        name: Identifier such as ``tz:+9`` or ``env-unset:API_URL``.
        description: Human-readable explanation of the condition.
        env_set: Environment variables to set for the child.
        env_unset: Environment variables to remove for the child.
        isolated_cwd: Run the child in an empty working directory.
        isolated_tmp: Run the child with an empty temporary directory.
        child_locale: Locale to request for the child, if any.
        expect: Probe values the child must report for the condition to count as applied.
        unsupported_reason: Why this scenario cannot run here, if it cannot.
    """

    name: str
    description: str
    env_set: Mapping[str, str] = field(default_factory=dict, hash=False)
    env_unset: tuple[str, ...] = ()
    isolated_cwd: bool = False
    isolated_tmp: bool = False
    child_locale: str | None = None
    expect: Mapping[str, str] = field(default_factory=dict, hash=False)
    unsupported_reason: str | None = None

    def __post_init__(self) -> None:
        """Validate the name and environment, and coerce containers to immutable types."""
        if _SCENARIO_NAME_RE.fullmatch(self.name) is None:
            raise ValueError(f"invalid scenario name {self.name!r}")
        env_set = dict(self.env_set)
        env_unset = tuple(self.env_unset)
        for key in env_set:
            _check_env_name(key)
        for key in env_unset:
            _check_env_name(key)
        overlap = sorted(set(env_set) & set(env_unset))
        if overlap:
            raise ValueError(f"environment variable {overlap[0]!r} cannot be both set and unset")
        object.__setattr__(self, "env_set", MappingProxyType(dict(sorted(env_set.items()))))
        object.__setattr__(self, "env_unset", env_unset)
        object.__setattr__(
            self,
            "expect",
            MappingProxyType(dict(sorted(dict(self.expect).items()))),
        )

    @property
    def supported(self) -> bool:
        """True when this scenario can be run on the current machine."""
        return self.unsupported_reason is None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "name": self.name,
            "description": self.description,
            "env_set": dict(self.env_set),
            "env_unset": list(self.env_unset),
            "isolated_cwd": self.isolated_cwd,
            "isolated_tmp": self.isolated_tmp,
            "child_locale": self.child_locale,
            "expect": dict(self.expect),
            "unsupported_reason": self.unsupported_reason,
        }


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """The verdict for one scenario, with the run it was based on, if any.

    Attributes:
        scenario: Name of the scenario.
        status: Verdict for the scenario.
        reason: Short human-readable explanation of the verdict.
        run: The child run the verdict was based on, or None if no run happened.
    """

    scenario: str
    status: VerificationStatus
    reason: str
    run: RunResult | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "scenario": self.scenario,
            "status": str(self.status),
            "reason": self.reason,
            "run": self.run.to_dict() if self.run is not None else None,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """The complete verification of one test.

    Attributes:
        test_id: Identifier of the verified test.
        baseline: Runs made without any scenario.
        scenarios: One result per requested scenario, in request order.
        limitations: Statements of what this verification does not establish.
    """

    test_id: str
    baseline: tuple[RunResult, ...]
    scenarios: tuple[ScenarioResult, ...]
    limitations: tuple[str, ...] = DEFAULT_LIMITATIONS

    @property
    def regressions(self) -> tuple[ScenarioResult, ...]:
        """Scenario results whose status is REGRESSION, in order."""
        return tuple(r for r in self.scenarios if r.status is VerificationStatus.REGRESSION)

    @property
    def status(self) -> VerificationStatus:
        """Overall verdict, the most severe status among the scenarios."""
        if not self.scenarios:
            return VerificationStatus.INCONCLUSIVE
        statuses = {r.status for r in self.scenarios}
        if VerificationStatus.REGRESSION in statuses:
            return VerificationStatus.REGRESSION
        if VerificationStatus.ERROR in statuses:
            return VerificationStatus.ERROR
        if VerificationStatus.INCONCLUSIVE in statuses:
            return VerificationStatus.INCONCLUSIVE
        if VerificationStatus.NO_DIFFERENCE in statuses:
            return VerificationStatus.NO_DIFFERENCE
        return VerificationStatus.UNSUPPORTED

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed key order."""
        return {
            "test_id": self.test_id,
            "status": str(self.status),
            "baseline": [run.to_dict() for run in self.baseline],
            "scenarios": [result.to_dict() for result in self.scenarios],
            "limitations": list(self.limitations),
        }


def assess_baseline(runs: Sequence[RunResult]) -> tuple[VerificationStatus | None, str]:
    """Decide whether the baseline runs are usable for comparison.

    Returns ``(None, "")`` when every baseline run passed. Otherwise returns the
    status that every scenario should receive and the reason for it.

    Raises:
        ValueError: If ``runs`` is empty.
    """
    if not runs:
        raise ValueError("assess_baseline requires at least one baseline run")
    for run in runs:
        if run.outcome is RunOutcome.ERROR:
            return (VerificationStatus.ERROR, f"baseline run error (exit code {run.exit_code})")
    if any(run.outcome is RunOutcome.TIMEOUT for run in runs):
        return (VerificationStatus.INCONCLUSIVE, "baseline timed out")
    if any(run.outcome is RunOutcome.NO_TESTS for run in runs):
        return (VerificationStatus.ERROR, "test not found: no tests collected in baseline")
    outcomes = {run.outcome for run in runs}
    if len(outcomes) > 1:
        names = ", ".join(sorted(outcome.value for outcome in outcomes))
        return (VerificationStatus.INCONCLUSIVE, "baseline is flaky: " + names)
    if outcomes <= {RunOutcome.FAILED, RunOutcome.COLLECTION_ERROR}:
        return (VerificationStatus.INCONCLUSIVE, "baseline is failing; nothing to compare against")
    return (None, "")


def check_probe(expected: Mapping[str, str], probe: Mapping[str, str] | None) -> str | None:
    """Return why the expected environment was not observed, or None if it was.

    Keys are checked in sorted order and the first mismatch is reported.
    """
    if not expected:
        return None
    if probe is None:
        return "child did not report its environment"
    for key in sorted(expected):
        actual = probe.get(key)
        if actual != expected[key]:
            return (
                f"condition did not take effect: {key} expected {expected[key]!r}, got {actual!r}"
            )
    return None


def _probe_verdict(scenario: Scenario, run: RunResult) -> tuple[VerificationStatus, str] | None:
    """Return the verdict forced by the environment probe, or None if the probe is fine."""
    if not scenario.expect:
        return None
    mismatch = check_probe(scenario.expect, run.probe)
    if run.probe is None:
        return (VerificationStatus.ERROR, mismatch or "child did not report its environment")
    if mismatch is not None:
        return (VerificationStatus.UNSUPPORTED, mismatch)
    return None


def _outcome_verdict(outcome: RunOutcome) -> tuple[VerificationStatus, str]:
    """Return the verdict for a run outcome, given a usable baseline and a healthy probe."""
    if outcome is RunOutcome.TIMEOUT:
        return (VerificationStatus.INCONCLUSIVE, "timed out under this scenario")
    if outcome is RunOutcome.NO_TESTS:
        return (VerificationStatus.ERROR, "test was not collected under this scenario")
    if outcome is RunOutcome.PASSED:
        return (
            VerificationStatus.NO_DIFFERENCE,
            "test passed under this scenario, as in the baseline",
        )
    if outcome is RunOutcome.FAILED:
        return (
            VerificationStatus.REGRESSION,
            "test passed in the baseline but failed under this scenario",
        )
    return (
        VerificationStatus.REGRESSION,
        "test passed in the baseline but failed to collect under this scenario",
    )


def classify(scenario: Scenario, run: RunResult | None) -> tuple[VerificationStatus, str]:
    """Classify one scenario run. Assumes the baseline was usable.

    Checks are applied in a fixed order; the first matching rule decides.
    """
    if scenario.unsupported_reason is not None:
        return (VerificationStatus.UNSUPPORTED, scenario.unsupported_reason)
    if run is None:
        return (VerificationStatus.ERROR, "scenario was not run")
    if run.outcome is RunOutcome.ERROR:
        return (VerificationStatus.ERROR, f"child process error (exit code {run.exit_code})")
    probe_verdict = _probe_verdict(scenario, run)
    if probe_verdict is not None:
        return probe_verdict
    return _outcome_verdict(run.outcome)


class Runner(Protocol):
    """Callable that executes a test, optionally under a scenario.

    ``scenario`` is None for a baseline run.
    """

    def __call__(self, test_id: str, project_root: Path, scenario: Scenario | None) -> RunResult:
        """Execute the test and return its classified result."""
        ...


class Verifier:
    """Re-runs one test under scenarios and classifies each outcome.

    The verifier never modifies ``os.environ`` or the current working directory;
    all child-side changes are the runner's responsibility.
    """

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        *,
        runner: Runner,
        baseline_runs: int = 2,
    ) -> None:
        """Bind the verifier to a project root and a runner.

        Raises:
            VerificationError: If ``project_root`` is not an existing directory.
            ValueError: If ``baseline_runs`` is less than 1.
        """
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise VerificationError(f"project root is not a directory: {root}")
        if baseline_runs < 1:
            raise ValueError(f"baseline_runs must be >= 1, got {baseline_runs}")
        self._root = root
        self._runner = runner
        self._baseline_runs = baseline_runs

    def verify(self, test_id: str, scenarios: Sequence[Scenario]) -> VerificationResult:
        """Run the baseline, then each scenario, and return the classified result.

        Raises:
            ValueError: If ``test_id`` is blank or multi-line, or scenario names repeat.
            VerificationError: If a baseline run cannot be started.
        """
        if not test_id.strip() or "\n" in test_id or "\r" in test_id:
            raise ValueError("test_id must be a non-blank single line")
        scenario_list = list(scenarios)
        seen: set[str] = set()
        for scenario in scenario_list:
            if scenario.name in seen:
                raise ValueError(f"duplicate scenario name {scenario.name!r}")
            seen.add(scenario.name)

        baseline: list[RunResult] = []
        for _ in range(self._baseline_runs):
            try:
                baseline.append(self._runner(test_id, self._root, None))
            except Exception as exc:
                raise VerificationError(f"baseline run could not be started: {exc}") from exc

        status, reason = assess_baseline(baseline)
        results: list[ScenarioResult] = []
        if status is not None:
            results = [ScenarioResult(s.name, status, reason, None) for s in scenario_list]
        else:
            for scenario in scenario_list:
                if not scenario.supported:
                    verdict, text = classify(scenario, None)
                    results.append(ScenarioResult(scenario.name, verdict, text, None))
                    continue
                try:
                    run = self._runner(test_id, self._root, scenario)
                except Exception as exc:
                    results.append(
                        ScenarioResult(
                            scenario.name,
                            VerificationStatus.ERROR,
                            f"runner failed: {exc}",
                            None,
                        )
                    )
                    continue
                verdict, text = classify(scenario, run)
                results.append(ScenarioResult(scenario.name, verdict, text, run))

        return VerificationResult(test_id, tuple(baseline), tuple(results))
