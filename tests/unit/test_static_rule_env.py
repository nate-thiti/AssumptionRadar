"""Unit tests for AR-ENV-001 (required env var) and AR-ENV-002 (undeclared env var)."""

from __future__ import annotations

import ast
import hashlib

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import Confidence, EvidenceKind, Finding, Severity
from assumptionradar.static.imports import build_import_table
from assumptionradar.static.parsing import ParsedModule
from assumptionradar.static.rules import default_rules
from assumptionradar.static.rules.env import (
    RequiredEnvVarRule,
    UndeclaredEnvVarRule,
    collect_env_reads,
)


def parsed(source: str, relpath: str = "m.py") -> ParsedModule:
    tree = ast.parse(source)
    return ParsedModule(relpath, source, "0" * 64, tree, build_import_table(tree))


def required(source: str) -> list[Finding]:
    return sorted(RequiredEnvVarRule().check(parsed(source)), key=lambda f: f.sort_key)


def undeclared(source: str, declared: tuple[str, ...] = ()) -> list[Finding]:
    rule = UndeclaredEnvVarRule(declared)
    return sorted(rule.check(parsed(source)), key=lambda f: f.sort_key)


# ------------------------------------------------------------------ AR-ENV-001


def test_default_rules_include_env_001_but_not_002() -> None:
    assert [r.rule_id for r in default_rules()][:2] == ["AR-CWD-001", "AR-ENV-001"]
    assert "AR-ENV-002" not in [r.rule_id for r in default_rules()]


def test_required_identity() -> None:
    rule = RequiredEnvVarRule()
    assert (rule.rule_id, rule.version) == ("AR-ENV-001", "1")


def test_required_subscript_details() -> None:
    source = "import os\n\nclass C:\n    def m(self):\n        return os.environ['DB_URL']\n"
    [finding] = required(source)
    assert finding.rule_id == "AR-ENV-001"
    assert finding.title == "Required environment variable has no default"
    assert finding.category == "environment"
    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert finding.location is not None
    assert (finding.location.path, finding.location.line, finding.location.column) == (
        "m.py",
        5,
        16,
    )
    assert finding.location.symbol == "C.m"
    assert finding.metadata == {"name": "DB_URL", "access": "subscript"}
    assert finding.message == (
        "Environment variable 'DB_URL' is read with os.environ[...] and no default; "
        "it raises KeyError when the variable is not set"
    )
    assert finding.fingerprint == compute_fingerprint(
        "AR-ENV-001", "m.py", "C.m", "subscript 'DB_URL'"
    )
    assert [e.kind for e in finding.evidence] == [EvidenceKind.INFERRED]
    assert finding.evidence[0].method == "ast"
    assert finding.remediation
    assert finding.failure_scenario


def test_required_pop_and_dynamic_key() -> None:
    findings = required("import os\nos.environ.pop('T')\nos.environ[name]\n")
    assert [(f.metadata, f.confidence) for f in findings] == [
        ({"name": "T", "access": "pop"}, Confidence.HIGH),
        ({"name": "name", "access": "subscript"}, Confidence.MEDIUM),
    ]
    assert findings[0].message == (
        "Environment variable 'T' is read with os.environ.pop() and no default; "
        "it raises KeyError when the variable is not set"
    )
    assert findings[1].message.startswith("Environment variable name is read with")
    assert findings[0].location is not None
    assert findings[0].location.symbol is None


def test_required_resolves_aliases() -> None:
    assert len(required("from os import environ as e\ne['X']\n")) == 1
    assert required("environ = {}\nenviron['X']\n") == []  # not os.environ
    assert required("import os\nos.environb[b'X']\n") == []  # bytes API out of scope


def test_required_guards() -> None:
    assert required("import os\nif 'X' in os.environ:\n    os.environ['X']\n") == []
    assert required("import os\ntry:\n    os.environ['X']\nexcept KeyError:\n    pass\n") == []
    assert len(required("import os\ntry:\n    os.environ['X']\nexcept OSError:\n    pass\n")) == 1
    guarded_by_star = "import os\ntry:\n    os.environ['X']\nexcept* KeyError:\n    pass\n"
    assert required(guarded_by_star) == []


def test_required_fingerprint_is_stable_when_lines_move() -> None:
    [a] = required("import os\ndef f():\n    os.environ['X']\n")
    [b] = required("import os\n\n\ndef f():\n    y = 1\n    os.environ['X']\n")
    assert a.fingerprint == b.fingerprint


# ------------------------------------------------------------------ AR-ENV-002


def test_collect_env_reads() -> None:
    module = parsed(
        "import os\n"
        "from os import getenv\n"
        "def f():\n"
        "    os.environ['B']\n"
        "    os.environ.get('A')\n"
        "    getenv('A', 'x')\n"
        "    os.getenv(key='C')\n"
        "    os.environ.setdefault('D', '1')\n"
        "    'E' in os.environ\n"
        "    'F' not in os.environ\n"
        "    os.environ['W'] = '1'\n"
        "    del os.environ['W']\n"
        "    os.environ.pop('P')\n"
        "    os.environ[name]\n"
    )
    reads = collect_env_reads(module)
    assert list(reads) == ["A", "B", "C", "D", "E", "F"]  # sorted, literal keys only
    assert [(loc.line, loc.column) for loc in reads["A"]] == [(5, 5), (6, 5)]
    assert reads["B"][0].symbol == "f"
    assert all(loc.path == "m.py" for locs in reads.values() for loc in locs)


def test_undeclared_details() -> None:
    source = "import os\ndef f():\n    os.getenv('API_KEY')\n    os.getenv('API_KEY')\n"
    [finding] = undeclared(source)
    assert finding.rule_id == "AR-ENV-002"
    assert finding.title == "Undeclared environment variable"
    assert finding.category == "environment"
    assert finding.severity is Severity.INFO
    assert finding.confidence is Confidence.HIGH
    assert finding.location is not None
    assert (finding.location.line, finding.location.column, finding.location.symbol) == (
        3,
        5,
        "f",
    )
    assert finding.metadata == {"name": "API_KEY", "reads": "2"}
    assert (
        finding.message == "Environment variable 'API_KEY' is not declared (2 reads in this file)"
    )
    assert finding.fingerprint == compute_fingerprint("AR-ENV-002", "m.py", None, "API_KEY")
    [single] = undeclared("import os\nos.getenv('X')\n")
    assert single.message == "Environment variable 'X' is not declared (1 read in this file)"


def test_undeclared_respects_declared_names() -> None:
    source = "import os\nos.getenv('A')\nos.getenv('B')\nos.environ['C']\n"
    assert [f.metadata["name"] for f in undeclared(source)] == ["A", "B", "C"]
    assert [f.metadata["name"] for f in undeclared(source, ("A", "C"))] == ["B"]


def test_undeclared_version_reflects_declared_set() -> None:
    assert UndeclaredEnvVarRule(()).rule_id == "AR-ENV-002"
    assert UndeclaredEnvVarRule(()).version == "1"
    digest = hashlib.sha256(b"A\nB").hexdigest()[:12]
    assert UndeclaredEnvVarRule(("B", "A", "B")).version == f"1+{digest}"
    assert UndeclaredEnvVarRule(("A", "B")).version == UndeclaredEnvVarRule(("B", "A")).version
    assert UndeclaredEnvVarRule(("A",)).version != UndeclaredEnvVarRule(("A", "B")).version
