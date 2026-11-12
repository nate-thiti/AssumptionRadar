"""Unit tests for AR-PLAT-001 (platform-specific code without a platform check)."""

from __future__ import annotations

import ast

import pytest

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import Confidence, EvidenceKind, Finding, Severity
from assumptionradar.static.imports import build_import_table
from assumptionradar.static.parsing import ParsedModule
from assumptionradar.static.rules import default_rules
from assumptionradar.static.rules.plat import (
    POSIX_ONLY_APIS,
    POSIX_ONLY_MODULES,
    WINDOWS_ONLY_APIS,
    WINDOWS_ONLY_MODULES,
    PlatformSpecificRule,
    classify_path_literal,
)


def check(source: str) -> list[Finding]:
    tree = ast.parse(source)
    module = ParsedModule("m.py", source, "0" * 64, tree, build_import_table(tree))
    return sorted(PlatformSpecificRule().check(module), key=lambda f: f.sort_key)


def kinds(source: str) -> list[tuple[str, str]]:
    return [(f.metadata["kind"], f.metadata["value"]) for f in check(source)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C:\\data", "windows-path"),
        ("c:/data", "windows-path"),
        ("C:", "windows-path"),
        ("\\\\server\\share", "windows-path"),
        ("data\\input.csv", "windows-path"),
        ("\\root-relative", "windows-path"),
        ("/etc/hosts", "posix-path"),
        ("/", "posix-path"),
        ("data/input.csv", None),
        ("input.csv", None),
        ("", None),
        ("~/x", None),
        ("https://example.test/a", None),
    ],
)
def test_classify_path_literal(text: str, expected: str | None) -> None:
    assert classify_path_literal(text) == expected


def test_api_and_module_tables() -> None:
    assert {"os.fork", "os.getuid", "signal.SIGKILL"} <= POSIX_ONLY_APIS
    assert {"os.startfile", "ctypes.windll", "subprocess.CREATE_NO_WINDOW"} <= WINDOWS_ONLY_APIS
    assert {"fcntl", "pwd", "grp", "termios", "resource"} <= POSIX_ONLY_MODULES
    assert {"msvcrt", "winreg", "winsound", "_winapi"} <= WINDOWS_ONLY_MODULES
    assert not POSIX_ONLY_APIS & WINDOWS_ONLY_APIS
    assert "sys.platform" not in POSIX_ONLY_APIS | WINDOWS_ONLY_APIS


def test_identity_and_registry() -> None:
    rule = PlatformSpecificRule()
    assert (rule.rule_id, rule.version) == ("AR-PLAT-001", "1")
    assert [r.rule_id for r in default_rules()][:4] == [
        "AR-CWD-001",
        "AR-ENV-001",
        "AR-EXE-001",
        "AR-PLAT-001",
    ]


def test_windows_path_details() -> None:
    [finding] = check("def f():\n    open('C:\\\\data\\\\in.csv')\n")
    assert finding.rule_id == "AR-PLAT-001"
    assert finding.title == "Platform-specific code without a platform check"
    assert finding.category == "platform"
    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert finding.location is not None
    assert (finding.location.line, finding.location.column, finding.location.symbol) == (
        2,
        10,
        "f",
    )
    assert finding.metadata == {"kind": "windows-path", "value": "C:\\data\\in.csv"}
    assert finding.message == (
        "'C:\\\\data\\\\in.csv' is a Windows path; on Linux and macOS it does not "
        "point to the intended file"
    )
    assert finding.fingerprint == compute_fingerprint(
        "AR-PLAT-001", "m.py", "f", "windows-path 'C:\\\\data\\\\in.csv'"
    )
    assert [e.kind for e in finding.evidence] == [EvidenceKind.INFERRED]
    assert finding.evidence[0].method == "ast"
    assert "os.path.join" in finding.remediation
    assert finding.failure_scenario


def test_posix_path_details() -> None:
    [finding] = check("from pathlib import Path\nPath('/srv/x').mkdir()\n")
    assert finding.confidence is Confidence.MEDIUM
    assert finding.metadata == {"kind": "posix-path", "value": "/srv/x"}
    assert finding.message == (
        "'/srv/x' is a POSIX absolute path; on Windows it resolves against the current drive"
    )
    assert "tempfile" in finding.remediation
    assert finding.location is not None
    assert (finding.location.line, finding.location.column) == (2, 6)


def test_fstring_path_uses_leading_text() -> None:
    [finding] = check("def f(n):\n    open(f'D:\\\\out\\\\{n}.csv')\n")
    # ast.unparse renders f-strings slightly differently across Python versions.
    assert finding.metadata["kind"] == "windows-path"
    assert finding.metadata["value"].startswith("f")
    assert "{n}" in finding.metadata["value"]
    assert check("def f(root):\n    open(f'{root}\\\\x.csv')\n") == []


def test_api_details() -> None:
    [finding] = check("import os\n\nclass D:\n    def run(self):\n        return os.fork()\n")
    assert finding.metadata == {"kind": "posix-api", "value": "os.fork"}
    assert finding.confidence is Confidence.HIGH
    assert finding.message == (
        "os.fork only exists on POSIX systems; on Windows it raises AttributeError or ImportError"
    )
    assert finding.location is not None
    assert (finding.location.line, finding.location.column, finding.location.symbol) == (
        5,
        16,
        "D.run",
    )
    assert finding.fingerprint == compute_fingerprint(
        "AR-PLAT-001", "m.py", "D.run", "posix-api os.fork"
    )
    [win] = check("import os\nos.startfile('a.pdf')\n")
    assert win.metadata == {"kind": "windows-api", "value": "os.startfile"}
    assert win.message == (
        "os.startfile only exists on Windows; on Linux and macOS it raises "
        "AttributeError or ImportError"
    )
    assert "sys.platform" in win.remediation


def test_module_imports() -> None:
    assert kinds("import fcntl\n") == [("posix-api", "fcntl")]
    assert kinds("import winreg as r\nr.OpenKey\n") == [("windows-api", "winreg")]
    assert kinds("from pwd import getpwuid\ngetpwuid(0)\n") == [("posix-api", "pwd")]
    assert kinds("import curses.ascii\n") == [("posix-api", "curses")]
    assert kinds("from . import fcntl\n") == []  # relative import: a local module
    [finding] = check("import os\nimport fcntl\n")
    assert finding.location is not None
    assert (finding.location.line, finding.location.column) == (2, 1)


def test_from_import_usage_is_reported_once() -> None:
    assert kinds("from os import getuid\ngetuid()\ngetuid()\n") == [
        ("posix-api", "os.getuid"),
        ("posix-api", "os.getuid"),
    ]


def test_nested_attribute_reported_once() -> None:
    assert kinds("import ctypes\nctypes.windll.kernel32.Beep(1, 1)\n") == [
        ("windows-api", "ctypes.windll")
    ]


def test_path_contexts() -> None:
    assert kinds("import os\nopen(os.path.join('/opt', 'x\\\\y'))\n") == [
        ("posix-path", "/opt"),
        ("windows-path", "x\\y"),
    ]
    pure_windows = "from pathlib import PureWindowsPath\nopen(PureWindowsPath('C:\\\\x'))\n"
    assert kinds(pure_windows) == []
    assert kinds("from pathlib import PurePosixPath\nopen(PurePosixPath('/x'))\n") == []
    assert kinds("print('C:\\\\x')\n") == []
    assert kinds("from pathlib import PurePath\nopen(PurePath('/x'))\n") == [("posix-path", "/x")]
    assert kinds("import os\nopen(os.path.join('/a', 'b'))\n") == [("posix-path", "/a")]
    assert kinds("from pathlib import Path\n(Path('/x') / 'y').exists()\n") == [
        ("posix-path", "/x")
    ]
    assert kinds("from pathlib import Path\nopen(Path('a') / 'C:\\\\b')\n") == [
        ("windows-path", "C:\\b")
    ]


def test_building_a_path_is_not_an_access() -> None:
    assert kinds("from pathlib import Path\nPath('/home/bob')\n") == []
    assert kinds("from pathlib import PurePath\nPurePath('/x')\n") == []
    assert kinds("import os\nos.path.join('C:\\\\', 'x')\n") == []
    assert kinds("from pathlib import Path\nstr(Path('/etc') / 'hosts')\n") == []
    assert kinds("from pathlib import Path\nPath('/x').name\n") == []


def test_platform_reference_guards_scope_chain() -> None:
    assert check("import os, sys\nif sys.platform != 'win32':\n    os.fork()\n") == []
    assert check("import os\nIS_NT = os.name == 'nt'\ndef f():\n    os.startfile('x')\n") == []
    sibling = "import os, sys\ndef g():\n    sys.platform\ndef f():\n    os.fork()\n"
    assert kinds(sibling) == [("posix-api", "os.fork")]
    assert check("import os\nif hasattr(os, 'fork'):\n    os.fork()\n") == []
    assert check("import os, platform\nif platform.system() == 'Linux':\n    os.fork()\n") == []
    assert (
        check("from sys import platform\nimport os\nif platform == 'linux':\n    os.fork()\n") == []
    )


def test_type_checking_and_try_guards() -> None:
    assert check("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import winreg\n") == []
    assert check("import typing\nif typing.TYPE_CHECKING:\n    import fcntl\n") == []
    else_branch = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n    import fcntl\n"
    )
    assert kinds(else_branch) == [("posix-api", "fcntl")]
    assert check("try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\n") == []
    assert check("try:\n    import msvcrt\nexcept ModuleNotFoundError:\n    pass\n") == []
    assert kinds("try:\n    import fcntl\nexcept ValueError:\n    pass\n") == [
        ("posix-api", "fcntl")
    ]
    assert check("import os\ntry:\n    open('/proc/x')\nexcept OSError:\n    pass\n") == []


def test_cross_platform_names_are_ignored() -> None:
    source = "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\nos.sep\nsignal.SIGINT\n"
    assert check(source) == []
