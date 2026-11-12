"""Tests for reading project metadata used by project-aware static rules (P2-6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from assumptionradar.static.project import (
    ProjectInfo,
    load_project,
    normalize_distribution,
    requirement_name,
)
from assumptionradar.static.rules import default_rules


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("requests", "requests"),
        ("PyYAML", "pyyaml"),
        ("scikit_learn", "scikit-learn"),
        ("zope.interface", "zope-interface"),
        ("A__B--C..D", "a-b-c-d"),
    ],
)
def test_normalize_distribution(name: str, expected: str) -> None:
    assert normalize_distribution(name) == expected


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("requests", "requests"),
        ("requests>=2.0,<3", "requests"),
        ("Pillow[jpeg,tiff]>=10", "Pillow"),
        ("orjson; python_version < '3.14'", "orjson"),
        ("pkg @ https://example.test/pkg.whl", "pkg"),
        ("  spaced  ", "spaced"),
        ("zope.interface==6", "zope.interface"),
        ("", None),
        ("   ", None),
        (">=1.0", None),
    ],
)
def test_requirement_name(requirement: str, expected: str | None) -> None:
    assert requirement_name(requirement) == expected


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_project_without_pyproject(tmp_path: Path) -> None:
    write(tmp_path / "pkg" / "__init__.py")
    info = load_project(tmp_path)
    assert info == ProjectInfo(
        root=tmp_path,
        has_pyproject=False,
        name=None,
        declared=frozenset(),
        first_party=frozenset({"pkg"}),
    )


def test_project_with_all_dependency_sections(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        "[project]\n"
        'name = "My_Project"\n'
        'dependencies = ["requests>=2", "PyYAML"]\n'
        "[project.optional-dependencies]\n"
        "fast = [\"orjson; python_version < '3.14'\"]\n"
        'img = ["Pillow[jpeg]>=10"]\n'
        "[dependency-groups]\n"
        'test = ["numpy", {include-group = "lint"}]\n'
        'lint = ["ruff"]\n',
    )
    info = load_project(tmp_path)
    assert info.has_pyproject is True
    assert info.name == "My_Project"
    assert info.declared == frozenset(
        {"my-project", "requests", "pyyaml", "orjson", "pillow", "numpy", "ruff"}
    )


def test_first_party_names(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    write(tmp_path / "rootpkg" / "__init__.py")
    write(tmp_path / "src" / "srcpkg" / "__init__.py")
    write(tmp_path / "src" / "srcmod.py")
    write(tmp_path / "toplevel.py")
    write(tmp_path / "notapkg" / "data.txt")
    write(tmp_path / "tests" / "test_x.py")
    info = load_project(tmp_path)
    assert info.first_party == frozenset({"rootpkg", "srcpkg", "srcmod", "toplevel"})


def test_missing_sections_are_empty(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[tool.other]\nx = 1\n")
    info = load_project(tmp_path)
    assert info.has_pyproject is True
    assert info.name is None
    assert info.declared == frozenset()


def test_invalid_pyproject_raises(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[project\nname = ")
    with pytest.raises(ValueError, match=r"pyproject\.toml"):
        load_project(tmp_path)


def test_wrong_types_raise(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", '[project]\ndependencies = "requests"\n')
    with pytest.raises(ValueError, match="dependencies"):
        load_project(tmp_path)


def test_default_rules_with_project(tmp_path: Path) -> None:
    assert "AR-IMP-001" not in [r.rule_id for r in default_rules()]
    assert "AR-IMP-001" not in [r.rule_id for r in default_rules(load_project(tmp_path))]
    write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    ids = [r.rule_id for r in default_rules(load_project(tmp_path))]
    assert ids[-1] == "AR-IMP-001"
    assert ids[:-1] == [r.rule_id for r in default_rules()]
