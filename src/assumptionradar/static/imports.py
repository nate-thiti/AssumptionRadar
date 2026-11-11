"""Import table and name resolution for the static scanner (ADR-0005).

The table is flow-insensitive: every import statement in the module counts,
in source order, and the last binding of a name wins. Assignments are not
tracked.
"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

_BUILTIN_NAMES: Final[frozenset[str]] = frozenset(dir(builtins))


@dataclass(frozen=True, slots=True)
class ImportTable:
    """Mapping from local names to qualified names for one module.

    Attributes:
        names: Local name to qualified name, read-only.
        star_modules: Modules imported with ``from m import *``, in source order.
    """

    names: Mapping[str, str]
    star_modules: tuple[str, ...]

    def resolve(self, node: ast.expr) -> str | None:
        """Return the qualified name an expression refers to, if known.

        Args:
            node: A ``Name`` or ``Attribute`` expression.

        Returns:
            The qualified dotted name, ``builtins.<name>`` for unbound builtins,
            or None when the expression cannot be resolved.
        """
        if isinstance(node, ast.Name):
            bound = self.names.get(node.id)
            if bound is not None:
                return bound
            if node.id in _BUILTIN_NAMES:
                return f"builtins.{node.id}"
            return None
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            if base is None:
                return None
            return f"{base}.{node.attr}"
        return None


def build_import_table(tree: ast.Module) -> ImportTable:
    """Build the import table for a parsed module.

    Args:
        tree: The parsed module.

    Returns:
        The import table with the last binding of each name in source order.
    """
    statements = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    statements.sort(key=lambda node: (node.lineno, node.col_offset))

    names: dict[str, str] = {}
    star_modules: list[str] = []
    for node in statements:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    names[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    names[head] = head
            continue

        base = "." * node.level + (node.module or "")
        for alias in node.names:
            if alias.name == "*":
                star_modules.append(base)
                continue
            qualified = f"{base}.{alias.name}" if node.module is not None else f"{base}{alias.name}"
            names[alias.asname or alias.name] = qualified

    return ImportTable(
        names=MappingProxyType(dict(names)),
        star_modules=tuple(star_modules),
    )
