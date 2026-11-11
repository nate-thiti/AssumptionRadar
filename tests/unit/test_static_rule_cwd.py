"""Unit tests for AR-CWD-001 (relative path accessed at run time)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import Confidence, EvidenceKind, Finding, Severity
from assumptionradar.static.engine import scan
from assumptionradar.static.imports import build_import_table
from assumptionradar.static.parsing import ParsedModule
from assumptionradar.static.rules import default_rules
from assumptionradar.static.rules.cwd import RelativePathAccessRule, is_relative_path_literal


def check(source: str, relpath: str = "m.py") -> list[Finding]:
    tree = ast.parse(source)
    module = ParsedModule(relpath, source, "0" * 64, tree, build_import_table(tree))
    return sorted(RelativePathAccessRule().check(module), key=lambda f: f.sort_key)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("data.json", True),
        ("./x", True),
        ("../x", True),
        ("sub/dir/f.txt", True),
        ("sub\\f.txt", True),
        ("nul.txt", True),
        ("", False),
        ("/etc/hosts", False),
        ("\\x", False),
        ("\\\\srv\\share", False),
        ("C:\\x", False),
        ("c:/x", False),
        ("C:", False),
        ("~", False),
        ("~/x", False),
        (":memory:", False),
        ("NUL", False),
        ("nul", False),
        ("file:x.db", False),
        ("https://example.test/x", False),
        ("s3://bucket/key", False),
    ],
)
def test_is_relative_path_literal(text: str, expected: bool) -> None:
    assert is_relative_path_literal(text) is expected


def test_rule_identity() -> None:
    rule = RelativePathAccessRule()
    assert (rule.rule_id, rule.version) == ("AR-CWD-001", "1")
    rules = default_rules()
    assert [r.rule_id for r in rules] == ["AR-CWD-001"]
    assert default_rules()[0] is not rules[0]  # fresh instances every call


def test_method_finding_details() -> None:
    source = (
        "from pathlib import Path\n"
        "\n"
        "class C:\n"
        "    def m(self):\n"
        "        return Path('a.txt').read_text()\n"
    )
    [finding] = check(source)
    assert finding.rule_id == "AR-CWD-001"
    assert finding.title == "Relative path depends on the working directory"
    assert finding.category == "cwd"
    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert finding.location is not None
    assert (finding.location.path, finding.location.line, finding.location.column) == (
        "m.py",
        5,
        16,
    )
    assert finding.location.symbol == "C.m"
    assert finding.metadata == {"sink": "pathlib.Path.read_text", "path": "Path('a.txt')"}
    assert finding.message == (
        "pathlib.Path.read_text() is called with the relative path Path('a.txt'), "
        "which is resolved against the current working directory"
    )
    assert finding.fingerprint == compute_fingerprint(
        "AR-CWD-001", "m.py", "C.m", "pathlib.Path.read_text Path('a.txt')"
    )
    assert [e.kind for e in finding.evidence] == [EvidenceKind.INFERRED]
    assert finding.evidence[0].method == "ast"
    assert finding.remediation
    assert finding.failure_scenario


def test_builtin_open_details_at_module_level() -> None:
    [finding] = check('open("data.json")\n')
    assert finding.metadata == {"sink": "open", "path": "data.json"}
    assert finding.message == (
        "open() is called with the relative path 'data.json', "
        "which is resolved against the current working directory"
    )
    assert finding.location is not None
    assert finding.location.symbol is None
    assert (finding.location.line, finding.location.column) == (1, 1)
    assert finding.fingerprint == compute_fingerprint(
        "AR-CWD-001", "m.py", None, "open 'data.json'"
    )


def test_fstring_is_medium_confidence() -> None:
    [finding] = check("def f(n):\n    open(f'data/{n}.json')\n")
    assert finding.confidence is Confidence.MEDIUM
    assert finding.metadata == {"sink": "open", "path": "f'data/{n}.json'"}
    assert finding.location is not None
    assert finding.location.symbol == "f"


def test_qualified_sink_names() -> None:
    findings = check(
        "import io, os, shutil\n"
        "io.open('a')\n"
        "os.listdir('b')\n"
        "shutil.copy('c', '/abs/d')\n"
        "open(os.path.join('e', 'f'))\n"
    )
    assert [(f.metadata["sink"], f.metadata["path"]) for f in findings] == [
        ("io.open", "a"),
        ("os.listdir", "b"),
        ("shutil.copy", "c"),
        ("open", "os.path.join('e', 'f')"),
    ]


def test_second_argument_by_keyword() -> None:
    findings = check("import shutil\nshutil.copy(src='/abs/a', dst='b')\n")
    assert [(f.metadata["path"]) for f in findings] == ["b"]


def test_nested_function_symbol() -> None:
    [finding] = check("def outer():\n    def inner():\n        open('x')\n")
    assert finding.location is not None
    assert finding.location.symbol == "outer.inner"


def test_async_function_symbol() -> None:
    [finding] = check("async def f():\n    open('x')\n")
    assert finding.location is not None
    assert finding.location.symbol == "f"


def test_alias_and_shadowing_imports() -> None:
    assert len(check("from pathlib import Path as P\nP('x').exists()\n")) == 1
    assert len(check("from os import listdir as ls\nls('x')\n")) == 1
    assert check("from webbrowser import open\nopen('index.html')\n") == []


def test_fingerprint_is_stable_when_lines_move() -> None:
    [a] = check("def f():\n    open('x')\n")
    [b] = check("import os\n\n\ndef f():\n    x = 1\n    open('x')\n")
    assert a.fingerprint == b.fingerprint
    [c] = check("def g():\n    open('x')\n")
    assert c.fingerprint != a.fingerprint


def test_rule_runs_through_engine(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("open('x')\nopen('x')\n", encoding="utf-8")
    result = scan(tmp_path, default_rules())
    assert [f.location.line for f in result.findings if f.location] == [1, 2]
    assert result.findings[1].fingerprint == result.findings[0].fingerprint + ":2"


def test_empty_and_unrelated_module() -> None:
    assert check("") == []
    assert check("x = Path\nprint('hello.txt')\n") == []
