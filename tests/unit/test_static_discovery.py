"""Tests for static file discovery and glob matching (P2-1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from assumptionradar.static.discovery import (
    DEFAULT_EXCLUDES,
    Discovery,
    SkippedFile,
    discover,
    glob_match,
)


def touch(root: Path, rel: str, content: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def rels(result: Discovery, root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in result.files]


# ------------------------------------------------------------------ glob_match


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/*.py", "a.py", True),
        ("**/*.py", "pkg/sub/a.py", True),
        ("**/*.py", "a.pyc", False),
        ("*.py", "a.py", True),
        ("*.py", "pkg/a.py", True),  # no "/" in pattern: matches any segment
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/pkg/a.py", False),  # "*" does not cross "/"
        ("src/**/*.py", "src/a.py", True),
        ("src/**/*.py", "src/pkg/deep/a.py", True),
        ("src/**", "src/pkg/a.py", True),
        ("tests/fixtures/**", "tests/fixtures/p/t.py", True),
        ("tests/fixtures/**", "tests/test_x.py", False),
        (".venv", ".venv/lib/x.py", True),  # segment pattern matches a directory
        ("build", "src/build/x.py", True),
        ("build", "src/builder.py", False),
        ("*.egg-info", "pkg.egg-info/PKG-INFO", True),
        ("test_?.py", "test_a.py", True),
        ("test_?.py", "test_ab.py", False),
        ("a[b].py", "a[b].py", True),  # regex metacharacters are literal
        ("ü/*.py", "ü/x.py", True),
    ],
)
def test_glob_match(pattern: str, path: str, expected: bool) -> None:
    assert glob_match(pattern, path) is expected


# -------------------------------------------------------------------- discover


def test_finds_python_files_sorted(tmp_path: Path) -> None:
    for rel in ("b.py", "a.py", "pkg/z.py", "pkg/a.py", "notes.txt"):
        touch(tmp_path, rel)
    result = discover(tmp_path)
    assert rels(result, tmp_path) == ["a.py", "b.py", "pkg/a.py", "pkg/z.py"]
    assert result.skipped == ()
    assert all(p.is_absolute() for p in result.files)


def test_default_excludes(tmp_path: Path) -> None:
    for rel in (
        "ok.py",
        ".venv/lib/site.py",
        "venv/x.py",
        ".git/hooks/h.py",
        "build/lib/x.py",
        "dist/x.py",
        "node_modules/x.py",
        "pkg/__pycache__/x.py",
        ".tox/x.py",
        "pkg.egg-info/x.py",
        "lib/site-packages/x.py",
    ):
        touch(tmp_path, rel)
    assert rels(discover(tmp_path), tmp_path) == ["ok.py"]
    assert rels(discover(tmp_path, use_default_excludes=False), tmp_path) == sorted(
        [
            ".git/hooks/h.py",
            ".tox/x.py",
            ".venv/lib/site.py",
            "build/lib/x.py",
            "dist/x.py",
            "lib/site-packages/x.py",
            "node_modules/x.py",
            "ok.py",
            "pkg.egg-info/x.py",
            "pkg/__pycache__/x.py",
            "venv/x.py",
        ]
    )
    assert ".venv" in DEFAULT_EXCLUDES


def test_include_and_exclude(tmp_path: Path) -> None:
    for rel in ("src/a.py", "src/b.py", "tests/test_a.py", "tests/fixtures/f.py", "setup.py"):
        touch(tmp_path, rel)
    result = discover(
        tmp_path, include=("src/**/*.py", "tests/**/*.py"), exclude=("tests/fixtures/**",)
    )
    assert rels(result, tmp_path) == ["src/a.py", "src/b.py", "tests/test_a.py"]


def test_unicode_and_space_paths(tmp_path: Path) -> None:
    touch(tmp_path, "โฟลเดอร์ ทดสอบ/ไฟล์ a.py")
    touch(tmp_path, "my dir/x y.py")
    assert rels(discover(tmp_path), tmp_path) == ["my dir/x y.py", "โฟลเดอร์ ทดสอบ/ไฟล์ a.py"]


def test_large_files_are_skipped_with_reason(tmp_path: Path) -> None:
    touch(tmp_path, "small.py", "x = 1\n")
    big = touch(tmp_path, "big.py", "x = 1\n" * 100)
    result = discover(tmp_path, max_file_bytes=100)
    assert rels(result, tmp_path) == ["small.py"]
    assert result.skipped == (
        SkippedFile(big, f"larger than 100 bytes ({big.stat().st_size} bytes)"),
    )


def test_invalid_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_file_bytes"):
        discover(tmp_path, max_file_bytes=0)
    with pytest.raises(ValueError, match="not a directory"):
        discover(tmp_path / "missing")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_directories_are_not_followed(tmp_path: Path) -> None:
    touch(tmp_path, "real/a.py")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "real" / "loop").symlink_to(tmp_path, target_is_directory=True)
    assert rels(discover(tmp_path), tmp_path) == ["real/a.py"]
