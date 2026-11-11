"""Read and parse source files without executing them (ADR-0005).

Scanned code is only ever passed to ``ast.parse``; it is never imported,
executed or compiled to run.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assumptionradar.static.imports import ImportTable, build_import_table


@dataclass(frozen=True, slots=True)
class ScanError:
    """A file-level problem that prevented scanning a file.

    Attributes:
        relpath: POSIX path relative to the project root.
        kind: One of ``"syntax"``, ``"decode"``, ``"read"`` or ``"rule"``.
        message: Human-readable description.
        line: 1-based line of the problem, if known.
        column: 1-based column of the problem, if known.
    """

    relpath: str
    kind: str
    message: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "relpath": self.relpath,
            "kind": self.kind,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True, slots=True)
class ParsedModule:
    """A successfully parsed source file.

    Attributes:
        relpath: POSIX path relative to the project root.
        source: Decoded source text without a leading byte-order mark.
        source_hash: SHA-256 hex digest of the raw file bytes.
        tree: The parsed AST.
        imports: The import table built from ``tree``.
    """

    relpath: str
    source: str
    source_hash: str
    tree: ast.Module = field(compare=False, hash=False)
    imports: ImportTable = field(compare=False, hash=False)


def parse_file(path: Path, root: Path) -> ParsedModule | ScanError:
    """Read, decode and parse one Python source file.

    Args:
        path: Path of the file to parse.
        root: Project root used to compute the relative path.

    Returns:
        The parsed module, or a ``ScanError`` describing why it could not be parsed.
    """
    relpath = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        data = path.read_bytes()
    except OSError as exc:
        return ScanError(relpath, "read", str(exc))

    source_hash = hashlib.sha256(data).hexdigest()
    if b"\x00" in data:
        return ScanError(relpath, "syntax", "source code cannot contain null bytes")
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        source = data.decode(encoding)
    except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
        return ScanError(relpath, "decode", str(exc))
    source = source.removeprefix("﻿")

    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError as exc:
        return ScanError(relpath, "syntax", exc.msg, exc.lineno, exc.offset)
    except ValueError as exc:
        return ScanError(relpath, "syntax", str(exc))

    return ParsedModule(
        relpath=relpath,
        source=source,
        source_hash=source_hash,
        tree=tree,
        imports=build_import_table(tree),
    )
