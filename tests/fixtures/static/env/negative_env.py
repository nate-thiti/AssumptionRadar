"""AR-ENV-001 negative cases: nothing in this file may be reported.

This file is parsed, never executed.
"""

from __future__ import annotations

import os

import pytest

if "MODULE_VAR" in os.environ:
    VALUE = os.environ["MODULE_VAR"]


def with_default() -> str:
    return os.environ.get("A", "x") + os.getenv("B", "y")


def get_without_default() -> str | None:
    return os.environ.get("C") or os.getenv("D")


def guarded_by_if() -> str:
    if "E" in os.environ:
        return os.environ["E"]
    return ""


def guarded_by_not_in_and_raise() -> str:
    if "F" not in os.environ:
        raise RuntimeError("set F")
    return os.environ["F"]


def guarded_by_getenv() -> str:
    if os.getenv("G"):
        return os.environ["G"]
    return ""


def guarded_by_conditional_expression() -> str:
    return os.environ["H"] if "H" in os.environ else ""


def guarded_by_key_error() -> str:
    try:
        return os.environ["I"]
    except KeyError:
        return ""


def guarded_by_lookup_error_tuple() -> str:
    try:
        return os.environ["J"]
    except (ValueError, LookupError):
        return ""


def guarded_by_bare_except() -> str:
    try:
        return os.environ["K"]
    except:
        return ""


def guarded_by_exception() -> str:
    try:
        return os.environ["K2"]
    except Exception:
        return ""


def set_by_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("L", "1")
    return os.environ["L"]


def assigned_before_read() -> str:
    os.environ["M"] = "1"
    return os.environ["M"]


def setdefault_before_read() -> str:
    os.environ.setdefault("N", "1")
    return os.environ["N"]


def writes_and_deletes() -> None:
    os.environ["O"] = "1"
    os.environ["O"] += "2"
    del os.environ["O"]
    os.environ.pop("P", None)
    os.environ.pop("P2", default=None)


def copies_are_not_tracked() -> str:
    env = dict(os.environ)
    return env["Q"] + os.environ.copy()["R"]


def checked_in_enclosing_scope() -> str:
    if "S" in os.environ:

        def inner() -> str:
            return os.environ["S"]

        return inner()
    return ""


def dynamic_key_guarded(name: str) -> str:
    if name in os.environ:
        return os.environ[name]
    return ""


def other_mappings(data: dict[str, str]) -> str:
    return data["U"]
