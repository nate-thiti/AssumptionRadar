"""Tests for the public Python API (P2-12)."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

import assumptionradar
from assumptionradar import (
    AssumptionRadarError,
    Baseline,
    BaselineComparison,
    BaselineError,
    ConfigurationError,
    Finding,
    Radar,
    ScanReport,
    Settings,
    Severity,
    VerificationError,
    VerificationReport,
)
from assumptionradar.analysis.verification import RunOutcome, RunResult, Scenario
from assumptionradar.baseline import write_baseline
from assumptionradar.core.models import VerificationStatus

EXPECTED_ALL = [
    "AssumptionRadarError",
    "Baseline",
    "BaselineComparison",
    "BaselineError",
    "ConfigurationError",
    "Finding",
    "Radar",
    "ScanReport",
    "Settings",
    "Severity",
    "VerificationError",
    "VerificationReport",
    "__version__",
]

SOURCE = "import os\ndef load():\n    return open('config.json')\nos.environ['TOKEN']\n"


def project(tmp_path: Path, source: str = SOURCE, config: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "m.py").write_text(source, encoding="utf-8")
    if config is not None:
        (root / "assumptionradar.toml").write_text(config, encoding="utf-8")
    return root


# --------------------------------------------------------------- the package


def test_public_names() -> None:
    assert sorted(assumptionradar.__all__) == EXPECTED_ALL
    for name in assumptionradar.__all__:
        assert hasattr(assumptionradar, name), name
    assert dir(assumptionradar) == sorted(set(dir(assumptionradar)))
    assert set(EXPECTED_ALL) <= set(dir(assumptionradar))


def test_unknown_attribute() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'Nope'"):
        _ = assumptionradar.Nope  # module __getattr__ makes this type-check; it fails at run time


def test_importing_the_package_stays_light() -> None:
    code = (
        "import sys, assumptionradar\n"
        "heavy = [m for m in ('assumptionradar.static', 'assumptionradar.config', "
        "'assumptionradar.analysis') if m in sys.modules]\n"
        "print(heavy)\n"
        "assumptionradar.Radar\n"
        "print('assumptionradar.config' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert out == ["[]", "True"]


def test_public_objects_have_docstrings() -> None:
    for name in EXPECTED_ALL:
        if name == "__version__":
            continue
        obj = getattr(assumptionradar, name)
        assert inspect.getdoc(obj), name
    for cls in (Radar, ScanReport, VerificationReport):
        for attr, member in vars(cls).items():
            if attr.startswith("_"):
                continue
            target = member.fget if isinstance(member, property) else member
            if callable(target):
                assert inspect.getdoc(target), f"{cls.__name__}.{attr}"


def test_errors_share_one_base() -> None:
    for error in (ConfigurationError, BaselineError, VerificationError):
        assert issubclass(error, AssumptionRadarError)


# ---------------------------------------------------------------------- scan


def test_scan_returns_a_report(tmp_path: Path) -> None:
    root = project(tmp_path)
    report = Radar().scan(root)
    assert isinstance(report, ScanReport)
    assert report.root == root
    assert [f.rule_id for f in report.findings] == ["AR-CWD-001", "AR-ENV-001"]
    assert all(isinstance(f, Finding) for f in report.findings)
    assert report.suppressed == ()
    assert report.errors == ()
    assert report.failing is True
    assert report.summary() == "2 findings in 1 file"
    assert isinstance(report.settings, Settings)


def test_scan_accepts_str_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    assert len(Radar().scan(str(root)).findings) == 2
    monkeypatch.chdir(root)
    assert len(Radar().scan().findings) == 2


def test_report_exports(tmp_path: Path) -> None:
    report = Radar().scan(project(tmp_path))
    data = report.to_dict()
    assert data["kind"] == "assumptionradar-scan"
    assert json.loads(report.to_json()) == data
    assert report.to_json(indent=None) == json.dumps(data)
    sarif = report.to_sarif_dict()
    assert sarif["version"] == "2.1.0"
    assert json.loads(report.to_sarif()) == sarif


def test_render(tmp_path: Path) -> None:
    report = Radar().scan(project(tmp_path))
    text = report.render()
    assert text.splitlines()[0].startswith("m.py:3:12: AR-CWD-001 [warning] ")
    assert text.endswith("2 findings in 1 file\n")
    assert "\x1b[" not in text
    assert "    fix: " in report.render(verbose=True)
    assert report.render(color=True).endswith("\x1b[31m2 findings in 1 file\x1b[0m\n")


def test_options_override_config(tmp_path: Path) -> None:
    root = project(tmp_path, config='fail-on = "error"\nignore-rules = ["AR-ENV-001"]\n')
    report = Radar().scan(root)
    assert [f.rule_id for f in report.findings] == ["AR-CWD-001"]
    assert report.failing is False

    strict = Radar(fail_on="warning", ignore_rules=[]).scan(root)
    assert [f.rule_id for f in strict.findings] == ["AR-CWD-001", "AR-ENV-001"]
    assert strict.failing is True

    only = Radar(rules=["AR-ENV-001"]).scan(root)
    assert [f.rule_id for f in only.findings] == []  # still ignored by the config


def test_use_config_false_ignores_files(tmp_path: Path) -> None:
    root = project(tmp_path, config='rules = ["AR-ENV-001"]\n')
    assert len(Radar().scan(root).findings) == 1
    assert len(Radar(use_config=False).scan(root).findings) == 2
    assert Radar(use_config=False).scan(root).settings.config_file is None


def test_explicit_config_file(tmp_path: Path) -> None:
    root = project(tmp_path)
    custom = tmp_path / "custom.toml"
    custom.write_text('rules = ["AR-ENV-001"]\n', encoding="utf-8")
    report = Radar(config=custom).scan(root)
    assert [f.rule_id for f in report.findings] == ["AR-ENV-001"]
    assert report.settings.config_file == custom


def test_config_and_use_config_false_conflict() -> None:
    with pytest.raises(ValueError, match="use_config=False"):
        Radar(config="x.toml", use_config=False)


def test_invalid_options_fail_at_construction() -> None:
    with pytest.raises(ConfigurationError) as info:
        Radar(rules=["AR-CWD-01"])
    assert (
        str(info.value)
        == "Radar: 'rules' contains unknown rule id 'AR-CWD-01'; did you mean 'AR-CWD-001'?"
    )
    with pytest.raises(ConfigurationError, match=r"^Radar: 'fail-on' must be one of"):
        Radar(fail_on="loud")


def test_bad_config_file_raises(tmp_path: Path) -> None:
    root = project(tmp_path, config="excludes = []\n")
    with pytest.raises(ConfigurationError, match="did you mean 'exclude'"):
        Radar().scan(root)


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="project root is not a directory"):
        Radar().scan(tmp_path / "nope")


def test_cache_option(tmp_path: Path) -> None:
    root = project(tmp_path)
    Radar(cache=False).scan(root)
    assert not (root / ".assumptionradar_cache").exists()
    Radar().scan(root)
    assert (root / ".assumptionradar_cache").is_dir()


def test_strict_suppressions_option(tmp_path: Path) -> None:
    root = project(tmp_path, "x = 1  # assumptionradar: ignore\n")
    assert Radar().scan(root).failing is False
    assert Radar(strict_suppressions=True).scan(root).failing is True


def test_severity_threshold_with_enum(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert Radar(fail_on=Severity.ERROR).scan(root).failing is False


# ------------------------------------------------------------------ baseline


def test_baseline_round_trip(tmp_path: Path) -> None:
    root = project(tmp_path)
    radar = Radar()
    baseline = radar.scan(root).baseline()
    assert isinstance(baseline, Baseline)
    assert len(baseline.entries) == 2

    (root / "m.py").write_text(SOURCE + "open('new.json')\n", encoding="utf-8")
    comparison = radar.scan(root).compare(baseline)
    assert isinstance(comparison, BaselineComparison)
    assert (len(comparison.new), len(comparison.unchanged), len(comparison.resolved)) == (1, 2, 0)


def test_compare_with_a_file(tmp_path: Path) -> None:

    root = project(tmp_path)
    path = tmp_path / "baseline.json"
    write_baseline(path, Radar().scan(root).baseline())
    comparison = Radar().scan(root).compare(path)
    assert comparison.new == ()
    assert len(Radar().scan(root).compare(str(path)).unchanged) == 2
    with pytest.raises(BaselineError, match="baseline not found"):
        Radar().scan(root).compare(tmp_path / "missing.json")


# -------------------------------------------------------------------- verify


class FakeRunner:
    """Passes the baseline; scenario outcomes come from a table."""

    def __init__(self, outcomes: Mapping[str, RunOutcome]) -> None:
        self.outcomes = dict(outcomes)
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, test_id: str, project_root: Path, scenario: Scenario | None) -> RunResult:
        self.calls.append((test_id, scenario.name if scenario else None))
        if scenario is None:
            return RunResult(RunOutcome.PASSED, 0, 0.1)
        outcome = self.outcomes.get(scenario.name, RunOutcome.PASSED)
        code = 0 if outcome is RunOutcome.PASSED else 1
        probe = {key: value for key, value in scenario.expect.items()}
        return RunResult(outcome, code, 0.1, "", probe)


def test_verify_with_a_runner(tmp_path: Path) -> None:
    runner = FakeRunner({"cwd": RunOutcome.FAILED})
    report = Radar().verify(
        ["tests/test_a.py::test_one", "tests/test_a.py::test_two"],
        root=tmp_path,
        scenarios="cwd",
        runner=runner,
    )
    assert isinstance(report, VerificationReport)
    assert [r.test_id for r in report.results] == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two",
    ]
    assert [r.status for r in report.results] == [VerificationStatus.REGRESSION] * 2
    assert report.regressions == report.results
    assert report.failing is True
    assert report.summary() == "2 tests verified: 2 regressions"
    assert runner.calls[:3] == [
        ("tests/test_a.py::test_one", None),
        ("tests/test_a.py::test_one", None),
        ("tests/test_a.py::test_one", "cwd"),
    ]


def test_verify_single_id_and_scenario_list(tmp_path: Path) -> None:
    runner = FakeRunner({})
    report = Radar().verify(
        "tests/test_a.py::test_one",
        root=tmp_path,
        scenarios=["cwd", "tmpdir"],
        runner=runner,
        baseline_runs=1,
    )
    (result,) = report.results
    assert [s.scenario for s in result.scenarios] == ["cwd", "tmpdir"]
    assert result.status is VerificationStatus.NO_DIFFERENCE
    assert report.failing is False
    assert report.regressions == ()
    assert report.summary() == "1 test verified: 1 no difference"
    assert [name for _, name in runner.calls] == [None, "cwd", "tmpdir"]


def test_verification_report_exports(tmp_path: Path) -> None:
    report = Radar().verify(
        "t.py::t", root=tmp_path, scenarios="cwd", runner=FakeRunner({"cwd": RunOutcome.FAILED})
    )
    data = report.to_dict()
    assert data["schema_version"] == "1"
    assert data["observation"] is None
    verifications = data["verifications"]
    assert isinstance(verifications, list)
    assert [v["status"] for v in verifications] == ["regression"]
    assert json.loads(report.to_json()) == data


def test_verification_summary_counts() -> None:
    assert VerificationReport(()).summary() == "0 tests verified"


def test_verify_input_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one test id"):
        Radar().verify([], root=tmp_path, runner=FakeRunner({}))
    with pytest.raises(ConfigurationError, match=r"^invalid scenarios: "):
        Radar().verify("t.py::t", root=tmp_path, scenarios="nope", runner=FakeRunner({}))
    with pytest.raises(VerificationError, match="project root"):
        Radar().verify("t.py::t", root=tmp_path / "missing", runner=FakeRunner({}))
