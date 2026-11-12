"""Project metadata read from ``pyproject.toml`` and the first-party layout.

The loader reads declared distributions (dependencies, extras and dependency
groups) and the names of first-party importable modules. It never executes
the project's code.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_SEPARATORS_RE: Final = re.compile(r"[-_.]+")
_REQUIREMENT_NAME_RE: Final = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize_distribution(name: str) -> str:
    """Return the normalized form of a distribution name.

    Args:
        name: A distribution name as written in a requirement or project table.

    Returns:
        The name with runs of ``-``, ``_`` and ``.`` replaced by ``-``, lowercased.
    """
    return _SEPARATORS_RE.sub("-", name).lower()


def requirement_name(requirement: str) -> str | None:
    """Return the distribution name at the start of a PEP 508 requirement string.

    Args:
        requirement: A requirement such as ``"requests>=2"`` or ``"pkg @ url"``.

    Returns:
        The name as written, or None when the string does not start with one.
    """
    match = _REQUIREMENT_NAME_RE.match(requirement)
    return match.group(1) if match else None


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Project metadata relevant to static rules.

    Attributes:
        root: The project root directory.
        has_pyproject: True when ``pyproject.toml`` exists in the root.
        name: The ``[project]`` name, if declared.
        declared: Normalized distribution names declared by the project.
        first_party: Importable top-level names inside the project.
    """

    root: Path
    has_pyproject: bool
    name: str | None
    declared: frozenset[str]
    first_party: frozenset[str]


def _first_party(root: Path) -> frozenset[str]:
    """Return the top-level importable names found directly under ``root`` and ``root/src``.

    Args:
        root: The project root directory.

    Returns:
        Package directories containing ``__init__.py`` and stems of ``*.py`` files.
    """
    names: set[str] = set()
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir():
                if (child / "__init__.py").is_file():
                    names.add(child.name)
            elif child.is_file() and child.suffix == ".py":
                names.add(child.stem)
    return frozenset(names)


def _str_list(value: object, label: str) -> list[str]:
    """Return ``value`` as a list of strings or raise.

    Args:
        value: The TOML value to check.
        label: The dotted key name used in the error message.

    Returns:
        The strings in order.

    Raises:
        ValueError: If ``value`` is not a list of strings.
    """
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} must be a list of strings")
        items.append(item)
    return items


def _requirements(data: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Return the project name and every requirement string declared in ``data``.

    Args:
        data: The parsed ``pyproject.toml`` document.

    Returns:
        The ``[project]`` name if it is a string, and the requirement strings.

    Raises:
        ValueError: If a dependency section has an unexpected shape.
    """
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise ValueError("[project] must be a table")
    name = project.get("name")
    project_name = name if isinstance(name, str) else None

    requirements = _str_list(project.get("dependencies", []), "project.dependencies")

    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise ValueError("project.optional-dependencies must be a table of lists of strings")
    for group in optional.values():
        requirements.extend(_str_list(group, "project.optional-dependencies"))

    groups = data.get("dependency-groups", {})
    if not isinstance(groups, dict):
        raise ValueError("dependency-groups must be a table of lists")
    for group in groups.values():
        if not isinstance(group, list):
            raise ValueError("dependency-groups must be a table of lists")
        for item in group:
            if isinstance(item, dict):
                continue
            if not isinstance(item, str):
                raise ValueError(
                    "dependency-groups entries must be strings or include-group tables"
                )
            requirements.append(item)
    return project_name, requirements


def load_project(root: Path) -> ProjectInfo:
    """Read the project metadata of ``root``.

    Args:
        root: The project root directory.

    Returns:
        The project metadata. Without a ``pyproject.toml`` the declared set and
        name are empty, but first-party names are still computed.

    Raises:
        ValueError: If ``pyproject.toml`` is not valid TOML or has the wrong shape.
    """
    first_party = _first_party(root)
    path = root / "pyproject.toml"
    if not path.is_file():
        return ProjectInfo(
            root=root,
            has_pyproject=False,
            name=None,
            declared=frozenset(),
            first_party=first_party,
        )

    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid pyproject.toml at {path}: {exc}") from exc

    project_name, requirements = _requirements(data)
    declared: set[str] = set()
    for requirement in requirements:
        name = requirement_name(requirement)
        if name is not None:
            declared.add(normalize_distribution(name))
    if project_name:
        declared.add(normalize_distribution(project_name))

    return ProjectInfo(
        root=root,
        has_pyproject=True,
        name=project_name,
        declared=frozenset(declared),
        first_party=first_party,
    )
