"""Unit tests for AR-EXE-001 (command looked up on PATH)."""

from __future__ import annotations

import ast

import pytest

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import Confidence, EvidenceKind, Finding, Severity
from assumptionradar.static.imports import build_import_table
from assumptionradar.static.parsing import ParsedModule
from assumptionradar.static.rules import default_rules
from assumptionradar.static.rules.exe import (
    PathLookupRule,
    is_bare_program,
    program_from_command,
)


def check(source: str) -> list[Finding]:
    tree = ast.parse(source)
    module = ParsedModule("m.py", source, "0" * 64, tree, build_import_table(tree))
    return sorted(PathLookupRule().check(module), key=lambda f: f.sort_key)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("git", True),
        ("redis-cli", True),
        ("python3.12", True),
        ("git.exe", True),
        ("", False),
        ("/usr/bin/git", False),
        ("./build.sh", False),
        ("scripts/build.sh", False),
        ("scripts\\build.bat", False),
        ("C:\\Tools\\git.exe", False),
        ("C:git", False),
        ("$EDITOR", False),
        ("%COMSPEC%", False),
        ("~/bin/tool", False),
        ("git status", False),
    ],
)
def test_is_bare_program(name: str, expected: bool) -> None:
    assert is_bare_program(name) is expected


@pytest.mark.parametrize(
    ("text", "shell", "expected"),
    [
        ("git", False, "git"),
        ("  git  status ", False, "git"),
        ("/bin/ls -la", False, None),
        ("", False, None),
        ("   ", True, None),
        ("ffmpeg -version", True, "ffmpeg"),
        ("FOO=1 BAR=2 java -version", True, "java"),
        ("FOO=1", True, None),
        ("FOO=1 java", False, None),  # no shell: the first token is the program
        ("cd /tmp && make", True, None),  # shell builtin first
        ("echo hi", True, None),
        ("echo hi", False, "echo"),  # without a shell, echo is a real program lookup
        ("dir /b", True, None),  # cmd.exe builtin
        ("$EDITOR notes.txt", True, None),
    ],
)
def test_program_from_command(text: str, shell: bool, expected: str | None) -> None:
    assert program_from_command(text, shell=shell) == expected


def test_rule_identity_and_registry() -> None:
    rule = PathLookupRule()
    assert (rule.rule_id, rule.version) == ("AR-EXE-001", "1")
    assert "AR-EXE-001" in [r.rule_id for r in default_rules()]


def test_list_form_details() -> None:
    source = (
        "import subprocess\n"
        "\n"
        "class C:\n"
        "    def m(self):\n"
        "        return subprocess.run(['git', 'status'])\n"
    )
    [finding] = check(source)
    assert finding.rule_id == "AR-EXE-001"
    assert finding.title == "Command is looked up on PATH"
    assert finding.category == "executables"
    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert finding.location is not None
    assert (finding.location.line, finding.location.column, finding.location.symbol) == (
        5,
        16,
        "C.m",
    )
    assert finding.metadata == {"sink": "subprocess.run", "program": "git", "shell": "false"}
    assert finding.message == (
        "subprocess.run() runs 'git' by name, so the result depends on the PATH "
        "of the machine that runs it"
    )
    assert finding.fingerprint == compute_fingerprint(
        "AR-EXE-001", "m.py", "C.m", "subprocess.run git"
    )
    assert [e.kind for e in finding.evidence] == [EvidenceKind.INFERRED]
    assert finding.evidence[0].method == "ast"
    assert finding.remediation
    assert finding.failure_scenario


def test_shell_forms_are_medium_confidence() -> None:
    findings = check(
        "import os, subprocess\n"
        "os.system('make all')\n"
        "subprocess.run('make all', shell=True)\n"
        "subprocess.run('make')\n"
    )
    assert [(f.metadata["sink"], f.metadata["shell"], f.confidence) for f in findings] == [
        ("os.system", "true", Confidence.MEDIUM),
        ("subprocess.run", "true", Confidence.MEDIUM),
        ("subprocess.run", "false", Confidence.HIGH),
    ]


def test_shell_false_keyword_and_non_constant_shell() -> None:
    [a] = check("import subprocess\nsubprocess.run('make all', shell=False)\n")
    assert a.metadata["shell"] == "false"
    assert a.metadata["program"] == "make"
    [b] = check("import subprocess\nsubprocess.run('cd x', shell=flag)\n")
    assert b.metadata == {"sink": "subprocess.run", "program": "cd", "shell": "false"}


def test_python_program_has_specific_remediation() -> None:
    [finding] = check("import subprocess\nsubprocess.run(['python', '-V'])\n")
    assert "sys.executable" in finding.remediation
    [other] = check("import subprocess\nsubprocess.run(['git'])\n")
    assert "shutil.which" in other.remediation


def test_spawn_family_uses_second_argument() -> None:
    [finding] = check("import os\nos.spawnlp(os.P_WAIT, 'ls', 'ls')\n")
    assert finding.metadata == {"sink": "os.spawnlp", "program": "ls", "shell": "false"}
    assert check("import os\nos.spawnl(os.P_WAIT, 'ls', 'ls')\n") == []  # no PATH lookup


def test_guard_scopes() -> None:
    guarded = (
        "import shutil, subprocess\n"
        "def f():\n    shutil.which('git')\n    subprocess.run(['git'])\n"
    )
    assert check(guarded) == []
    from_module = (
        "import shutil, subprocess\nshutil.which('git')\ndef f():\n    subprocess.run(['git'])\n"
    )
    assert check(from_module) == []
    sibling = (
        "import shutil, subprocess\n"
        "def g():\n    shutil.which('git')\n"
        "def f():\n    subprocess.run(['git'])\n"
    )
    assert len(check(sibling)) == 1
    aliased = "from shutil import which\nimport subprocess\nwhich('git')\nsubprocess.run(['git'])\n"
    assert check(aliased) == []


def test_unresolved_or_local_run_is_ignored() -> None:
    assert check("def run(x): pass\nrun(['git'])\n") == []
    assert check("from mylib import run\nrun(['git'])\n") == []


def test_fingerprint_is_stable_when_lines_move() -> None:
    [a] = check("import subprocess\ndef f():\n    subprocess.run(['git'])\n")
    [b] = check("import subprocess\n\n\ndef f():\n    x = 1\n    subprocess.run(['git'])\n")
    assert a.fingerprint == b.fingerprint
