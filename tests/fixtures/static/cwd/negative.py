"""AR-CWD-001 negative cases: nothing in this file may be reported.

This file is parsed, never executed.
"""

from __future__ import annotations

import glob
import os
import sqlite3
import tempfile
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path("data.json")  # building a path does not access it
NAME = "settings.json"


def anchored_to_module() -> str:
    return (HERE / "data.json").read_text(encoding="utf-8")


def anchored_with_dunder_file() -> str:
    return (Path(__file__).parent / "fixtures" / "a.txt").read_text(encoding="utf-8")


def os_path_anchored() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    open(os.path.join(base, "x.txt"), encoding="utf-8").close()


def absolute_paths() -> None:
    open("/etc/hosts", encoding="utf-8").close()
    open("C:\\Windows\\win.ini", encoding="utf-8").close()
    open("C:/Windows/win.ini", encoding="utf-8").close()
    open("\\\\server\\share\\f.txt", encoding="utf-8").close()
    Path("/var/log").exists()


def special_names() -> None:
    open(os.devnull, "w", encoding="utf-8").close()
    open("NUL", "w", encoding="utf-8").close()
    sqlite3.connect(":memory:").close()
    sqlite3.connect("file:app.db?mode=ro", uri=True).close()
    Path("~/.config/app.toml").expanduser().read_text(encoding="utf-8")


def names_are_not_tracked(path: str) -> None:
    open(path, encoding="utf-8").close()
    open(NAME, encoding="utf-8").close()
    DATA.exists()


def fixtures_and_temp(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("x", encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "x.txt"), "w", encoding="utf-8").close()


def fstring_with_root(root: str) -> None:
    open(f"{root}/x.txt", encoding="utf-8").close()
    open(f"/abs/{root}.txt", encoding="utf-8").close()


def explicit_directory_handles() -> None:
    glob.glob("*.py", root_dir=HERE)
    fd = os.open(str(HERE), os.O_RDONLY)
    os.stat("x.txt", dir_fd=fd)


def non_access_path_methods() -> None:
    Path("a/b.txt").with_suffix(".bak")
    print(Path("a").name)
    Path("rel").resolve()
    Path("rel").absolute()
    print(str(Path("x") / "y"))
    (Path.cwd() / "x").read_text(encoding="utf-8")
    Path().exists()


def strings_are_not_paths() -> None:
    print("a.txt".replace("a", "b"))
    print(os.path.join("a", "b").replace("/", "-"))


def other_functions_named_open() -> None:
    webbrowser.open("index.html")
    open(0, encoding="utf-8").close()


def starred_arguments(parts: list[str]) -> None:
    os.listdir(*parts)
