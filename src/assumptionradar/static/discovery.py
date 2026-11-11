"""File discovery for the static scanner (ADR-0005).

Walks a project root, keeps files matching include globs and drops files and
directories matching exclude globs. Symlinked directories are not followed.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_EXCLUDES: Final[tuple[str, ...]] = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".nox",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "*.egg-info",
    "site-packages",
)


@functools.lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a glob pattern into a compiled regular expression.

    Args:
        pattern: Glob pattern using ``*``, ``?`` and ``**``.

    Returns:
        A compiled pattern to be used with ``fullmatch``.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def glob_match(pattern: str, relpath: str) -> bool:
    """Return True when ``pattern`` matches ``relpath``.

    A pattern without ``/`` matches when it matches any single path segment.
    A pattern with ``/`` must match the whole POSIX relative path, where ``**/``
    spans zero or more directories and a trailing ``**`` spans anything.

    Args:
        pattern: Glob pattern.
        relpath: POSIX path relative to the project root.

    Returns:
        True when the pattern matches.
    """
    regex = _compile(pattern)
    if "/" in pattern:
        return regex.fullmatch(relpath) is not None
    return any(regex.fullmatch(segment) is not None for segment in relpath.split("/"))


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """A file that matched the include rules but was not scanned.

    Attributes:
        path: Absolute path of the file.
        reason: Human-readable reason for skipping.
    """

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class Discovery:
    """Result of walking a project root.

    Attributes:
        files: Absolute paths of the selected files, sorted by relative path.
        skipped: Files that were selected but skipped, sorted by relative path.
    """

    files: tuple[Path, ...]
    skipped: tuple[SkippedFile, ...]


def _is_excluded(relpath: str, patterns: Sequence[str]) -> bool:
    """Return True when any exclude pattern matches ``relpath``."""
    return any(glob_match(pattern, relpath) for pattern in patterns)


def discover(
    root: Path,
    *,
    include: Sequence[str] = ("**/*.py",),
    exclude: Sequence[str] = (),
    use_default_excludes: bool = True,
    max_file_bytes: int = 1_000_000,
) -> Discovery:
    """Find the files under ``root`` that the static scanner should read.

    Args:
        root: Project root directory.
        include: Glob patterns a file must match at least one of.
        exclude: Glob patterns that remove matching files and directories.
        use_default_excludes: Also apply ``DEFAULT_EXCLUDES``.
        max_file_bytes: Files larger than this are reported as skipped.

    Returns:
        The selected files and the skipped files, both sorted by relative path.

    Raises:
        ValueError: If ``max_file_bytes`` is below 1 or ``root`` is not a directory.
    """
    if max_file_bytes < 1:
        raise ValueError(f"max_file_bytes must be >= 1, got {max_file_bytes}")
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")

    excludes: tuple[str, ...] = tuple(exclude)
    if use_default_excludes:
        excludes = excludes + DEFAULT_EXCLUDES
    includes: tuple[str, ...] = tuple(include)
    base = root.resolve()

    kept: list[tuple[str, Path]] = []
    skipped: list[tuple[str, SkippedFile]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_excluded((current / name).relative_to(root).as_posix(), excludes)
        ]
        for name in filenames:
            rel = (current / name).relative_to(root).as_posix()
            if not any(glob_match(pattern, rel) for pattern in includes):
                continue
            if _is_excluded(rel, excludes):
                continue
            path = base / rel
            size = (current / name).stat().st_size
            if size > max_file_bytes:
                reason = f"larger than {max_file_bytes} bytes ({size} bytes)"
                skipped.append((rel, SkippedFile(path, reason)))
            else:
                kept.append((rel, path))

    kept.sort(key=lambda item: item[0])
    skipped.sort(key=lambda item: item[0])
    return Discovery(
        files=tuple(path for _, path in kept),
        skipped=tuple(item for _, item in skipped),
    )
