"""AR-IMP-001 positive cases (declared distributions are in pyproject.toml next to this file).

Every line with an expect marker comment must produce exactly the listed
findings; unmarked lines must produce none. This file is parsed, never executed,
and is excluded from mypy because it imports packages that are not installed.
"""

from __future__ import annotations

import importlib

import pytest

try:
    import ujson  # expect: AR-IMP-001
except ImportError:
    ujson = None

try:
    from rich.console import Console  # expect: AR-IMP-001
except ModuleNotFoundError:
    Console = None

try:
    import sklearn.linear_model  # expect: AR-IMP-001
except (ImportError, AttributeError):
    pass

try:
    import lxml, msgpack  # expect: AR-IMP-001, AR-IMP-001
except Exception:
    pass


def lazy() -> object:
    try:
        return importlib.import_module("tqdm")  # expect: AR-IMP-001
    except ImportError:
        return None


pandas = pytest.importorskip("pandas")  # expect: AR-IMP-001


def test_needs_scipy() -> None:
    stats = pytest.importorskip("scipy.stats")  # expect: AR-IMP-001
    assert stats
