"""Session-time implementation of the pytest plugin (loaded only when requested).

The always-loaded entry point, :mod:`assumptionradar.integrations.pytest_plugin`,
imports this module and calls :func:`activate` only when an ``--assumption-*``
option is given, so ordinary pytest runs do not import the analysis engine.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from assumptionradar.analysis.runner import SubprocessRunner
from assumptionradar.analysis.runtime import Observation, RuntimeObserver
from assumptionradar.analysis.scenarios import parse_scenario_spec
from assumptionradar.analysis.verification import Scenario, VerificationResult, Verifier
from assumptionradar.core.exceptions import VerificationError
from assumptionradar.core.models import VerificationStatus
from assumptionradar.reporting.json_report import build_pytest_report, write_json
from assumptionradar.reporting.terminal import render_report

_STATE_KEY = pytest.StashKey["_State"]()
_NOT_VERIFIED_REASON = "did not pass in this session"
_LIMIT_REASON = "verification limit reached"


@dataclass(frozen=True)
class _Options:
    """Validated command-line options for one session."""

    observe: bool
    track_env_reads: bool
    verify_spec: str | None
    verify_max: int
    timeout_s: float
    strict: bool
    report: str | None


@dataclass
class _State:
    """Per-session plugin state, stored in ``config.stash``."""

    options: _Options
    scenarios: tuple[Scenario, ...] = ()
    report_path: Path | None = None
    observer: RuntimeObserver | None = None
    observation: Observation | None = None
    outcomes: dict[str, str] = field(default_factory=dict)
    results: list[VerificationResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    report_error: str | None = None
    report_written: bool = False


def _state_of(config: pytest.Config) -> _State | None:
    """Return this session's state, or None when the plugin is inactive."""
    return config.stash.get(_STATE_KEY, None)


def activate(config: pytest.Config) -> None:
    """Validate options, create the session state and register the outcome recorder.

    The caller registers this module itself as a plugin afterwards."""
    observe = bool(config.getoption("assumption_observe"))
    env_reads = bool(config.getoption("assumption_env_reads"))
    verify_spec: str | None = config.getoption("assumption_verify")
    verify_max: int = config.getoption("assumption_verify_max")
    timeout_s: float = config.getoption("assumption_timeout")
    strict = bool(config.getoption("assumption_strict"))
    report: str | None = config.getoption("assumption_report")

    if env_reads and not observe:
        raise pytest.UsageError("--assumption-env-reads requires --assumption-observe")
    if verify_max < 1:
        raise pytest.UsageError("--assumption-verify-max must be at least 1")
    if timeout_s <= 0:
        raise pytest.UsageError("--assumption-timeout must be greater than 0")

    scenarios: tuple[Scenario, ...] = ()
    if verify_spec is not None:
        try:
            scenarios = parse_scenario_spec(verify_spec)
        except ValueError as exc:
            raise pytest.UsageError(f"--assumption-verify: {exc}") from exc

    active = observe or verify_spec is not None or report is not None
    numprocesses = config.getoption("numprocesses", None)
    if active and (numprocesses or hasattr(config, "workerinput")):
        raise pytest.UsageError(
            "assumptionradar: runtime observation and verification are not supported "
            "with pytest-xdist (-n) in this version; run without -n"
        )
    if not active:
        return

    options = _Options(
        observe=observe,
        track_env_reads=env_reads,
        verify_spec=verify_spec,
        verify_max=verify_max,
        timeout_s=timeout_s,
        strict=strict,
        report=report,
    )
    state = _State(options=options, scenarios=scenarios)
    if report is not None:
        state.report_path = Path(config.invocation_params.dir) / report
    config.stash[_STATE_KEY] = state
    config.pluginmanager.register(_OutcomeRecorder(state), name="assumptionradar-outcomes")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Start the runtime observer before collection so import-time events are kept."""
    state = _state_of(session.config)
    if state is None or not state.options.observe:
        return
    observer = RuntimeObserver(
        session.config.rootpath, track_env_reads=state.options.track_env_reads
    )
    observer.__enter__()
    state.observer = observer


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Attribute runtime events to the test whose protocol is running."""
    state = _state_of(item.config)
    observer = None if state is None else state.observer
    if observer is None:
        return (yield)
    observer.set_test(item.nodeid)
    try:
        return (yield)
    finally:
        observer.set_test(None)


class _OutcomeRecorder:
    """Per-session plugin that records the final outcome of each test.

    pytest hook implementations receive only hookspec arguments, so the session
    state is bound to this object instead of being looked up from the report.
    """

    def __init__(self, state: _State) -> None:
        """Bind the recorder to one session's state."""
        self._state = state

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Record the final outcome of each test for later verification."""
        outcomes = self._state.outcomes
        current = outcomes.get(report.nodeid)
        new: str | None = current
        if report.failed:
            new = "failed"
        elif report.skipped and current != "failed":
            new = "skipped"
        elif report.when == "call" and report.passed and current not in ("failed", "skipped"):
            new = "passed"
        if new is not None:
            outcomes[report.nodeid] = new


def _write_terminal_line(config: pytest.Config, text: str) -> None:
    """Write one line to the terminal reporter when one is available."""
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        # At session finish pytest's progress line ("..... [100%]") is still open and
        # write_line() does not always end it, so end it here when it is non-empty.
        if getattr(reporter, "_width_of_current_line", 0):
            reporter.write("\n")
        reporter.write_line(text)


def _stop_observer(state: _State) -> None:
    """Deactivate the observer and keep its observation."""
    observer = state.observer
    if observer is None:
        return
    try:
        observer.__exit__(None, None, None)
    finally:
        state.observation = observer.observation()
        state.observer = None


def _select_for_verification(state: _State) -> list[str]:
    """Split outcomes into tests to verify and tests recorded as skipped."""
    to_verify: list[str] = []
    for nodeid, outcome in state.outcomes.items():
        if outcome != "passed":
            state.skipped.append((nodeid, _NOT_VERIFIED_REASON))
        elif len(to_verify) >= state.options.verify_max:
            state.skipped.append((nodeid, _LIMIT_REASON))
        else:
            to_verify.append(nodeid)
    return to_verify


def _run_verification(state: _State, session: pytest.Session) -> None:
    """Verify selected passing tests under the requested scenarios."""
    to_verify = _select_for_verification(state)
    if not to_verify:
        return
    names = ", ".join(s.name for s in state.scenarios)
    _write_terminal_line(
        session.config,
        f"assumptionradar: verifying {len(to_verify)} test(s) under: {names}",
    )
    verifier = Verifier(
        session.config.rootpath,
        runner=SubprocessRunner(timeout_s=state.options.timeout_s),
    )
    for nodeid in to_verify:
        try:
            state.results.append(verifier.verify(nodeid, state.scenarios))
        except VerificationError as exc:
            state.skipped.append((nodeid, f"verification error: {exc}"))


def _write_report(state: _State) -> None:
    """Write the JSON report if requested. Failures are recorded, never raised."""
    path = state.report_path
    if path is None:
        return
    try:
        write_json(path, build_pytest_report(state.observation, state.results, state.skipped))
    except OSError as exc:
        state.report_error = f"could not write report to {path}: {exc}"
    else:
        state.report_written = True


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    """Finish observation, run verification, apply strict mode and write the report."""
    state = _state_of(session.config)
    if state is None:
        return
    _stop_observer(state)
    if state.options.verify_spec is not None:
        _run_verification(state, session)
    if state.options.strict and session.exitstatus == 0:
        has_regression = any(r.status is VerificationStatus.REGRESSION for r in state.results)
        if has_regression:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    _write_report(state)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Release the observer if the session ended without finishing it."""
    state = _state_of(config)
    if state is not None and state.observer is not None:
        _stop_observer(state)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print what was observed, verified and written."""
    state = _state_of(terminalreporter.config)
    if state is None:
        return
    terminalreporter.section("assumptionradar")
    data = build_pytest_report(state.observation, state.results, state.skipped)
    for line in render_report(data):
        terminalreporter.write_line(line.text, **({line.style: True} if line.style else {}))
    if state.report_error is not None:
        terminalreporter.write_line(f"assumptionradar: {state.report_error}", red=True)
    elif state.report_written and state.report_path is not None:
        terminalreporter.write_line(f"report written to {state.report_path}")
