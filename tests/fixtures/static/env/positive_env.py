"""AR-ENV-001 positive cases.

Every line with an expect marker comment must produce exactly the listed
findings; unmarked lines must produce none. This file is parsed, never executed.
"""

from __future__ import annotations

import os
from os import environ
from os import environ as env


def subscript() -> str:
    return os.environ["DATABASE_URL"]  # expect: AR-ENV-001


def imported_names() -> str:
    return environ["API_HOST"] + env["API_PORT"]  # expect: AR-ENV-001, AR-ENV-001


def dynamic_key(name: str) -> str:
    return os.environ[name]  # expect: AR-ENV-001


def pop_without_default() -> str:
    return os.environ.pop("ONE_SHOT_TOKEN")  # expect: AR-ENV-001


def checked_in_a_sibling_function() -> bool:
    return "HOME" in os.environ


def not_checked_here() -> str:
    return os.environ["HOME"]  # expect: AR-ENV-001


def wrong_exception_type() -> str:
    try:
        return os.environ["REGION"]  # expect: AR-ENV-001
    except ValueError:
        return "x"


def read_in_handler_is_not_protected() -> str:
    try:
        return "x"
    except KeyError:
        return os.environ["FALLBACK"]  # expect: AR-ENV-001


def other_key_checked() -> str:
    if "A" in os.environ:
        return os.environ["B"]  # expect: AR-ENV-001
    return ""


def check_in_nested_function_does_not_guard_outer() -> str:
    def has() -> bool:
        return "NESTED" in os.environ

    return os.environ["NESTED"] if has() else ""  # expect: AR-ENV-001


class Settings:
    url = os.environ["SERVICE_URL"]  # expect: AR-ENV-001


TIMEOUT = int(os.environ["TIMEOUT"])  # expect: AR-ENV-001
