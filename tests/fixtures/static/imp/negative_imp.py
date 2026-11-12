"""AR-IMP-001 negative cases: nothing in this file may be reported.

Declared distributions are in pyproject.toml next to this file. This file is
parsed, never executed, and is excluded from mypy.
"""

from __future__ import annotations

import lxml  # not optional: no try block

import pytest

try:
    import requests  # declared in [project] dependencies
except ImportError:
    requests = None

try:
    import yaml  # PyYAML is declared; import name differs from the distribution
except ImportError:
    yaml = None

try:
    import orjson  # declared in an extra with an environment marker
except ImportError:
    orjson = None

try:
    from PIL import Image  # Pillow[jpeg] is declared
except ImportError:
    Image = None

try:
    import numpy  # declared in a dependency group
except ImportError:
    numpy = None

try:
    import ruff  # declared in an included dependency group
except ImportError:
    ruff = None

try:
    import tomllib  # standard library
except ImportError:
    import tomli as tomllib  # in the handler: a required fallback, not optional

try:
    import localpkg  # first-party package next to this file
except ImportError:
    localpkg = None

try:
    import imp_fixture  # the project itself
except ImportError:
    imp_fixture = None

try:
    from . import sibling  # relative import
except ImportError:
    sibling = None

try:
    import msgpack  # the handler does not catch ImportError
except ValueError:
    pass

yaml_module = pytest.importorskip("yaml")


def dynamic(name: str) -> object:
    return pytest.importorskip(name)


print(lxml, tomllib)
