"""Tests for the SARIF 2.1.0 exporter (P2-10).

The official OASIS schema is vendored at ``tests/data/sarif-schema-2.1.0.json``
(errata01, sha256 c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import assumptionradar
from assumptionradar.core.models import Confidence, Finding, Severity, SourceLocation
from assumptionradar.reporting.sarif import (
    FINGERPRINT_KEY,
    SARIF_SCHEMA_URI,
    SARIF_VERSION,
    TOOL_URI,
    build_sarif,
    sarif_from_scan,
)
from assumptionradar.static.engine import ScanResult, scan
from assumptionradar.static.parsing import ScanError
from assumptionradar.static.rules import default_rules

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "data" / "sarif-schema-2.1.0.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft4Validator(SCHEMA)


def assert_valid(document: dict[str, Any]) -> None:
    errors = sorted(VALIDATOR.iter_errors(document), key=lambda e: list(e.path))
    assert errors == [], [f"{list(e.path)}: {e.message}" for e in errors]


def scanned(tmp_path: Path, files: dict[str, str]) -> ScanResult:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return scan(tmp_path, default_rules())


SOURCE = (
    "import os\nimport subprocess\n\n"
    "def load():\n    return open('config.json')\n\n"
    "os.environ['TOKEN']\n"
    "subprocess.run(['git', 'status'])\n"
)


def manual(**changes: object) -> Finding:
    base = Finding(
        rule_id="AR-CWD-001",
        title="Relative path",
        message="opens a relative path",
        category="cwd",
        severity=Severity.WARNING,
        confidence=Confidence.HIGH,
        fingerprint="0123456789abcdef",
        location=SourceLocation("pkg/m.py", 3, 5, "load"),
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


def run_of(document: dict[str, Any]) -> dict[str, Any]:
    runs = document["runs"]
    assert len(runs) == 1
    run: dict[str, Any] = runs[0]
    return run


# ---------------------------------------------------------------------- schema


def test_vendored_schema_is_the_official_one() -> None:
    assert SCHEMA["title"] == "Static Analysis Results Format (SARIF) Version 2.1.0 JSON Schema"
    jsonschema.Draft4Validator.check_schema(SCHEMA)


def test_real_scan_is_valid_sarif(tmp_path: Path) -> None:
    result = scanned(tmp_path, {"m.py": SOURCE})
    assert {f.rule_id for f in result.findings} == {"AR-CWD-001", "AR-ENV-001", "AR-EXE-001"}
    document = sarif_from_scan(result, root=tmp_path)
    assert_valid(document)


def test_empty_scan_is_valid_sarif(tmp_path: Path) -> None:
    document = build_sarif(())
    assert_valid(document)
    run = run_of(document)
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_everything_at_once_is_valid(tmp_path: Path) -> None:
    errors = (
        ScanError("bad.py", "syntax", "invalid syntax", 2),
        ScanError("x.py", "read", "denied"),
    )
    document = build_sarif(
        (manual(), manual(location=None, fingerprint="1111111111111111")),
        suppressed=(manual(fingerprint="2222222222222222"),),
        errors=errors,
        root=tmp_path,
    )
    assert_valid(document)


# ---------------------------------------------------------------- top level


def test_top_level_and_driver(tmp_path: Path) -> None:
    document = build_sarif((manual(),))
    assert list(document) == ["$schema", "version", "runs"]
    assert (
        document["$schema"] == SARIF_SCHEMA_URI == "https://json.schemastore.org/sarif-2.1.0.json"
    )
    assert document["version"] == SARIF_VERSION == "2.1.0"
    driver = run_of(document)["tool"]["driver"]
    assert driver["name"] == "AssumptionRadar"
    assert driver["version"] == assumptionradar.__version__
    assert driver["informationUri"] == TOOL_URI == "https://github.com/nate-thiti/AssumptionRadar"


def test_rules_are_sorted_and_indexed(tmp_path: Path) -> None:
    result = scanned(tmp_path, {"m.py": SOURCE})
    run = run_of(sarif_from_scan(result))
    ids = [rule["id"] for rule in run["tool"]["driver"]["rules"]]
    assert ids == ["AR-CWD-001", "AR-ENV-001", "AR-EXE-001"]
    for item in run["results"]:
        assert ids[item["ruleIndex"]] == item["ruleId"]


def test_rule_descriptor_fields() -> None:
    with_help = manual(remediation="build the path from __file__", docs_url="https://x.test/cwd")
    (rule,) = run_of(build_sarif((with_help,)))["tool"]["driver"]["rules"]
    assert rule == {
        "id": "AR-CWD-001",
        "shortDescription": {"text": "Relative path"},
        "help": {"text": "build the path from __file__"},
        "helpUri": "https://x.test/cwd",
        "defaultConfiguration": {"level": "warning"},
        "properties": {"category": "cwd"},
    }
    (bare,) = run_of(build_sarif((manual(),)))["tool"]["driver"]["rules"]
    assert "help" not in bare
    assert "helpUri" not in bare


def test_rule_descriptor_uses_the_first_finding_of_each_rule() -> None:
    first = manual(title="First title")
    second = manual(title="Second title", fingerprint="1111111111111111")
    (rule,) = run_of(build_sarif((first, second)))["tool"]["driver"]["rules"]
    assert rule["shortDescription"] == {"text": "First title"}


# ------------------------------------------------------------------- results


def test_result_fields() -> None:
    (item,) = run_of(build_sarif((manual(),)))["results"]
    assert item == {
        "ruleId": "AR-CWD-001",
        "ruleIndex": 0,
        "level": "warning",
        "message": {"text": "opens a relative path"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "pkg/m.py", "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": 3, "startColumn": 5},
                },
                "logicalLocations": [{"fullyQualifiedName": "load"}],
            }
        ],
        "partialFingerprints": {FINGERPRINT_KEY: "0123456789abcdef"},
        "properties": {"confidence": "high", "category": "cwd"},
    }
    assert FINGERPRINT_KEY == "assumptionradar/v1"


@pytest.mark.parametrize(
    ("severity", "level"),
    [(Severity.ERROR, "error"), (Severity.WARNING, "warning"), (Severity.INFO, "note")],
)
def test_levels(severity: Severity, level: str) -> None:
    run = run_of(build_sarif((manual(severity=severity),)))
    assert run["results"][0]["level"] == level
    assert run["tool"]["driver"]["rules"][0]["defaultConfiguration"] == {"level": level}


def test_partial_locations() -> None:
    no_symbol = manual(location=SourceLocation("m.py", 4, None, None))
    (item,) = run_of(build_sarif((no_symbol,)))["results"]
    assert item["locations"] == [
        {
            "physicalLocation": {
                "artifactLocation": {"uri": "m.py", "uriBaseId": "%SRCROOT%"},
                "region": {"startLine": 4},
            }
        }
    ]
    file_only = manual(location=SourceLocation("m.py"))
    (item,) = run_of(build_sarif((file_only,)))["results"]
    assert "region" not in item["locations"][0]["physicalLocation"]
    (item,) = run_of(build_sarif((manual(location=None),)))["results"]
    assert "locations" not in item


def test_uris_are_percent_encoded() -> None:
    finding = manual(location=SourceLocation("my dir/über#1.py", 1, 1))
    (item,) = run_of(build_sarif((finding,)))["results"]
    uri = item["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "my%20dir/%C3%BCber%231.py"


def test_results_keep_finding_order_then_suppressed() -> None:
    a = manual(fingerprint="aaaaaaaaaaaaaaaa")
    b = manual(fingerprint="bbbbbbbbbbbbbbbb")
    c = manual(fingerprint="cccccccccccccccc")
    results = run_of(build_sarif((b, a), suppressed=(c,)))["results"]
    prints = [r["partialFingerprints"][FINGERPRINT_KEY] for r in results]
    assert prints == ["bbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaa", "cccccccccccccccc"]
    assert "suppressions" not in results[0]
    assert results[2]["suppressions"] == [{"kind": "inSource"}]


def test_suppressed_findings_from_a_real_scan(tmp_path: Path) -> None:
    result = scanned(
        tmp_path,
        {"m.py": "open('a.json')  # assumptionradar: ignore[AR-CWD-001] -- fine\nopen('b.json')\n"},
    )
    document = sarif_from_scan(result)
    assert_valid(document)
    results = run_of(document)["results"]
    assert [("suppressions" in r) for r in results] == [False, True]


# ------------------------------------------------------- run-level properties


def test_original_uri_base_ids(tmp_path: Path) -> None:
    run = run_of(build_sarif((manual(),), root=tmp_path))
    base = run["originalUriBaseIds"]["%SRCROOT%"]["uri"]
    assert base == tmp_path.absolute().as_uri() + "/"
    assert "originalUriBaseIds" not in run_of(build_sarif((manual(),)))


def test_invocation_and_notifications() -> None:
    errors = (
        ScanError("bad.py", "syntax", "invalid syntax", 2),
        ScanError("x y.py", "read", "permission denied"),
    )
    run = run_of(build_sarif((), errors=errors))
    (invocation,) = run["invocations"]
    assert invocation["executionSuccessful"] is True
    assert invocation["toolExecutionNotifications"] == [
        {
            "level": "error",
            "message": {"text": "bad.py could not be scanned (syntax): invalid syntax"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "bad.py", "uriBaseId": "%SRCROOT%"},
                        "region": {"startLine": 2},
                    }
                }
            ],
        },
        {
            "level": "error",
            "message": {"text": "x y.py could not be scanned (read): permission denied"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "x%20y.py", "uriBaseId": "%SRCROOT%"}
                    }
                }
            ],
        },
    ]


def test_no_errors_means_no_notifications() -> None:
    (invocation,) = run_of(build_sarif((manual(),)))["invocations"]
    assert invocation == {"executionSuccessful": True, "toolExecutionNotifications": []}


def test_output_is_deterministic(tmp_path: Path) -> None:
    result = scanned(tmp_path, {"m.py": SOURCE})
    first = json.dumps(sarif_from_scan(result, root=tmp_path))
    second = json.dumps(sarif_from_scan(result, root=tmp_path))
    assert first == second


def test_sarif_from_scan_matches_build_sarif(tmp_path: Path) -> None:
    result = scanned(tmp_path, {"m.py": SOURCE, "bad.py": "def f(:\n"})
    assert sarif_from_scan(result, root=tmp_path) == build_sarif(
        result.findings, suppressed=result.suppressed, errors=result.errors, root=tmp_path
    )
