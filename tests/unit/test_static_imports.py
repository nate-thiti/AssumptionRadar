"""Tests for the import table and name resolution (P2-1)."""

from __future__ import annotations

import ast

import pytest

from assumptionradar.static.imports import ImportTable, build_import_table


def table(source: str) -> ImportTable:
    return build_import_table(ast.parse(source))


def resolve(source: str, expr: str) -> str | None:
    node = ast.parse(expr, mode="eval").body
    return table(source).resolve(node)


@pytest.mark.parametrize(
    ("source", "expr", "expected"),
    [
        ("import os", "os.environ", "os.environ"),
        ("import os", "os.path.join", "os.path.join"),
        ("import os.path", "os.path.join", "os.path.join"),
        ("import os.path as osp", "osp.join", "os.path.join"),
        ("from os import environ", "environ", "os.environ"),
        ("from os import environ as env", "env.get", "os.environ.get"),
        ("from pathlib import Path", "Path", "pathlib.Path"),
        ("from pathlib import Path as P", "P", "pathlib.Path"),
        ("import subprocess as sp", "sp.run", "subprocess.run"),
        ("", "open", "builtins.open"),
        ("", "print", "builtins.print"),
        ("", "undefined_name", None),
        ("", "undefined_name.attr", None),
        ("from io import open", "open", "io.open"),  # import shadows the builtin
        ("import json", "json.load", "json.load"),
        ("from . import sibling", "sibling.f", ".sibling.f"),
        ("from ..pkg import mod as m", "m.f", "..pkg.mod.f"),
        ("import os", "os.environ['X']", None),  # subscripts are not names
        ("import os", "f().x", None),
        ("def f():\n    import shutil", "shutil.which", "shutil.which"),  # nested import
        ("import a\nimport b as a", "a.x", "b.x"),  # last binding wins
    ],
)
def test_resolve(source: str, expr: str, expected: str | None) -> None:
    assert resolve(source, expr) == expected


def test_names_and_star_imports() -> None:
    t = table("import os\nfrom pathlib import Path as P\nfrom typing import *\nfrom .x import *\n")
    assert dict(t.names) == {"os": "os", "P": "pathlib.Path"}
    assert t.star_modules == ("typing", ".x")


def test_resolve_call_target_helper() -> None:
    t = table("from pathlib import Path")
    call = ast.parse("Path('a').read_text()", mode="eval").body
    assert isinstance(call, ast.Call)
    # The method is called on the result of Path(...): resolve the inner call's func.
    assert isinstance(call.func, ast.Attribute)
    inner = call.func.value
    assert isinstance(inner, ast.Call)
    assert t.resolve(inner.func) == "pathlib.Path"
