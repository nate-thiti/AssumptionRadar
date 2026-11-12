"""Unit tests for AR-IMP-001 (optional import not declared in the project)."""

from __future__ import annotations

import ast
import hashlib

import pytest

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import Confidence, EvidenceKind, Finding, Severity
from assumptionradar.static.imports import build_import_table
from assumptionradar.static.parsing import ParsedModule
from assumptionradar.static.rules.optional_imports import (
    OptionalImportRule,
    distribution_candidates,
)


def check(
    source: str,
    declared: tuple[str, ...] = (),
    first_party: tuple[str, ...] = (),
) -> list[Finding]:
    tree = ast.parse(source)
    module = ParsedModule("m.py", source, "0" * 64, tree, build_import_table(tree))
    rule = OptionalImportRule(declared, first_party)
    return sorted(rule.check(module), key=lambda f: f.sort_key)


def modules(source: str, declared: tuple[str, ...] = ()) -> list[str]:
    return sorted(f.metadata["module"] for f in check(source, declared))


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("requests", {"requests"}),
        ("requests.adapters", {"requests"}),
        ("yaml", {"yaml", "pyyaml"}),
        ("PIL.Image", {"pil", "pillow"}),
        ("sklearn.linear_model", {"sklearn", "scikit-learn"}),
        ("cv2", {"cv2", "opencv-python", "opencv-python-headless"}),
        ("google.protobuf", {"google", "protobuf"}),
        ("my_pkg", {"my-pkg"}),
        ("xdist", {"xdist", "pytest-xdist"}),  # pytest plugins import under another name
        ("pkg_resources", {"pkg-resources", "setuptools"}),
    ],
)
def test_distribution_candidates(module: str, expected: set[str]) -> None:
    assert distribution_candidates(module) == frozenset(expected)


def test_identity_and_version() -> None:
    rule = OptionalImportRule((), ())
    assert (rule.rule_id, rule.version) == ("AR-IMP-001", "1")
    digest = hashlib.sha256(b"a\nb\x00c").hexdigest()[:12]
    assert OptionalImportRule(("b", "a", "a"), ("c",)).version == f"1+{digest}"
    assert OptionalImportRule(("a",), ()).version != OptionalImportRule((), ("a",)).version


def test_try_import_details() -> None:
    source = "def f():\n    try:\n        import ujson\n    except ImportError:\n        pass\n"
    [finding] = check(source)
    assert finding.rule_id == "AR-IMP-001"
    assert finding.title == "Optional dependency is not declared"
    assert finding.category == "imports"
    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.MEDIUM
    assert finding.location is not None
    assert (finding.location.line, finding.location.column, finding.location.symbol) == (
        3,
        9,
        "f",
    )
    assert finding.metadata == {"module": "ujson", "how": "try-import", "distribution": "ujson"}
    assert finding.message == (
        "'ujson' is imported as an optional dependency, but no dependency, extra or "
        "dependency group in pyproject.toml declares it"
    )
    assert finding.fingerprint == compute_fingerprint("AR-IMP-001", "m.py", "f", "try-import ujson")
    assert [e.kind for e in finding.evidence] == [EvidenceKind.INFERRED]
    assert "[project.optional-dependencies]" in finding.remediation
    assert "ujson" in finding.remediation
    assert finding.failure_scenario


def test_importorskip_details() -> None:
    [finding] = check("import pytest\nnp = pytest.importorskip('numpy.linalg')\n")
    assert finding.metadata == {
        "module": "numpy.linalg",
        "how": "importorskip",
        "distribution": "numpy",
    }
    assert finding.message == (
        "pytest.importorskip('numpy.linalg') skips the tests when 'numpy.linalg' is "
        "missing, but no dependency, extra or dependency group in pyproject.toml declares it"
    )
    assert finding.location is not None
    assert (finding.location.line, finding.location.column) == (2, 6)


def test_distribution_metadata_uses_alias() -> None:
    [finding] = check("try:\n    import yaml\nexcept ImportError:\n    pass\n")
    assert finding.metadata["distribution"] == "pyyaml"
    assert "pyyaml" in finding.remediation


def test_import_forms() -> None:
    src = (
        "import importlib\n"
        "try:\n"
        "    import a.b as ab, c\n"
        "    from d.e import f\n"
        "    importlib.import_module('g')\n"
        "    importlib.import_module(name)\n"
        "    from . import h\n"
        "except ImportError:\n"
        "    import i\n"
        "else:\n"
        "    import j\n"
        "finally:\n"
        "    import k\n"
    )
    assert modules(src) == ["a.b", "c", "d.e", "g"]


def test_handlers() -> None:
    template = "try:\n    import x\nexcept {}:\n    pass\n"
    for handler in ("ImportError", "ModuleNotFoundError", "(ValueError, ImportError)", "Exception"):
        assert modules(template.format(handler)) == ["x"], handler
    assert modules("try:\n    import x\nexcept:\n    pass\n") == ["x"]
    assert modules(template.format("ValueError")) == []
    star = "try:\n    import x\nexcept* ImportError:\n    pass\n"
    assert modules(star) == ["x"]


def test_declared_stdlib_and_first_party_are_ignored() -> None:
    src = "try:\n    import yaml, json, mypkg, requests.adapters\nexcept ImportError:\n    pass\n"
    assert check(src, declared=("pyyaml", "requests"), first_party=("mypkg",)) == []
    assert modules(src, declared=("requests",)) == ["mypkg", "yaml"]
    assert modules("try:\n    import __future__\nexcept ImportError:\n    pass\n") == []


def test_importorskip_needs_a_literal() -> None:
    assert modules("import pytest\npytest.importorskip(name)\n") == []
    assert modules("from pytest import importorskip\nimportorskip('zmq')\n") == ["zmq"]
