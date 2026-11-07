"""Tests for the report JSON Schema (P1-12)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest

from assumptionradar.analysis.runtime import EventKind, Observation, ObservedEvent
from assumptionradar.analysis.verification import (
    RunOutcome,
    RunResult,
    ScenarioResult,
    VerificationResult,
)
from assumptionradar.core.models import SourceLocation, VerificationStatus
from assumptionradar.reporting.json_report import (
    SCHEMA_VERSION,
    build_pytest_report,
    load_report_schema,
)

SCHEMA = load_report_schema()
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def full_report() -> dict[str, Any]:
    """A report that uses every optional branch: null and non-null alike."""
    observation = Observation(
        events=(
            ObservedEvent(
                EventKind.FILE_OPEN,
                "data.txt",
                {"mode": "r", "relative": "true"},
                SourceLocation("tests/test_a.py", line=3, column=1, symbol="test_a"),
                "tests/test_a.py::test_a",
            ),
            ObservedEvent(EventKind.ENV_READ, "HOME", {"present": "true"}, None, None),
        ),
        dropped=2,
        unattributed=1,
        errors=("handler failed",),
    )
    passed = RunResult(RunOutcome.PASSED, 0, 0.5, "1 passed", {"utc_offset_seconds": "0"})
    verification = VerificationResult(
        "tests/test_a.py::test_a",
        baseline=(passed, passed),
        scenarios=(
            ScenarioResult(
                "cwd", VerificationStatus.REGRESSION, "failed", RunResult(RunOutcome.FAILED, 1, 0.4)
            ),
            ScenarioResult(
                "tz:+9",
                VerificationStatus.INCONCLUSIVE,
                "timed out",
                RunResult(RunOutcome.TIMEOUT, None, 120.0),
            ),
            ScenarioResult("locale:de_DE", VerificationStatus.UNSUPPORTED, "not installed"),
        ),
    )
    report = build_pytest_report(observation, [verification], [("tests/test_b.py::t", "x")])
    data: dict[str, Any] = json.loads(json.dumps(report))
    return data


def errors(data: object) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors(data)]


class TestSchemaDocument:
    def test_is_valid_draft_2020_12(self) -> None:
        jsonschema.Draft202012Validator.check_schema(SCHEMA)

    def test_version_matches_code(self) -> None:
        assert SCHEMA["properties"]["schema_version"] == {"const": SCHEMA_VERSION}  # type: ignore[index]

    @pytest.mark.parametrize(
        ("pointer", "enum"),
        [
            (("$defs", "event", "properties", "kind", "enum"), EventKind),
            (("$defs", "run", "properties", "outcome", "enum"), RunOutcome),
            (("$defs", "verification_status", "enum"), VerificationStatus),
        ],
    )
    def test_enums_match_code(self, pointer: tuple[str, ...], enum: type[Any]) -> None:
        node: Any = SCHEMA
        for key in pointer:
            node = node[key]
        assert node == [member.value for member in enum]


class TestValidReports:
    def test_full_report(self) -> None:
        assert errors(full_report()) == []

    def test_empty_report(self) -> None:
        report = json.loads(json.dumps(build_pytest_report(None, [], [])))
        assert errors(report) == []


def _set(path: tuple[Any, ...], value: object) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        node: Any = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _delete(path: tuple[Any, ...]) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        node: Any = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


EVENT0 = ("observation", "events", 0)
VER0 = ("verifications", 0)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_set(("schema_version",), "2"), id="wrong-version"),
        pytest.param(_set(("schema_version",), 1), id="version-not-string"),
        pytest.param(_set(("extra",), True), id="unknown-top-level-key"),
        pytest.param(_delete(("verifications",)), id="missing-verifications"),
        pytest.param(_set(("tool", "name"), "other"), id="wrong-tool-name"),
        pytest.param(_set((*EVENT0, "kind"), "file_read"), id="unknown-event-kind"),
        pytest.param(_set((*EVENT0, "attributes", "mode"), 1), id="attribute-not-string"),
        pytest.param(_set((*EVENT0, "location", "path"), "/abs/x.py"), id="absolute-path"),
        pytest.param(_set((*EVENT0, "location", "path"), "C:/x.py"), id="drive-path"),
        pytest.param(_set((*EVENT0, "location", "path"), "a\\b.py"), id="backslash-path"),
        pytest.param(_set((*EVENT0, "location", "path"), "../x.py"), id="parent-path"),
        pytest.param(_set((*EVENT0, "location", "line"), 0), id="line-zero"),
        pytest.param(_set(("observation", "dropped"), -1), id="negative-dropped"),
        pytest.param(_set((*VER0, "status"), "passed"), id="unknown-status"),
        pytest.param(_set((*VER0, "baseline"), []), id="empty-baseline"),
        pytest.param(_set((*VER0, "limitations"), []), id="no-limitations"),
        pytest.param(_set((*VER0, "baseline", 0, "outcome"), "ok"), id="unknown-outcome"),
        pytest.param(_set((*VER0, "baseline", 0, "duration_s"), -0.1), id="negative-duration"),
        pytest.param(_set((*VER0, "baseline", 0, "probe", "x"), 1), id="probe-not-string"),
        pytest.param(_delete(("verification_skipped", 0, "reason")), id="skip-without-reason"),
    ],
)
def test_invalid_reports_are_rejected(mutate: Callable[[dict[str, Any]], None]) -> None:
    data = copy.deepcopy(full_report())
    mutate(data)
    assert errors(data) != []
