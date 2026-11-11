"""AR-CWD-001 positive cases.

Every line with an expect marker comment must produce exactly the listed
findings; unmarked lines must produce none. This file is parsed, never executed.
"""

from __future__ import annotations

import codecs
import glob
import io
import json
import os
import os.path
import shutil
import sqlite3
from pathlib import Path
from pathlib import Path as P


def builtin_open() -> str:
    with open("config/settings.json", encoding="utf-8") as handle:  # expect: AR-CWD-001
        return handle.read()


def json_from_open() -> object:
    return json.load(open("data.json", encoding="utf-8"))  # expect: AR-CWD-001


def keyword_file() -> None:
    open(file="out.txt", mode="w", encoding="utf-8").close()  # expect: AR-CWD-001


def other_openers() -> None:
    io.open("a.txt", encoding="utf-8").close()  # expect: AR-CWD-001
    codecs.open("b.txt", encoding="utf-8").close()  # expect: AR-CWD-001
    os.close(os.open("c.txt", os.O_RDONLY))  # expect: AR-CWD-001
    open("./dot.txt", encoding="utf-8").close()  # expect: AR-CWD-001
    open("../parent.txt", encoding="utf-8").close()  # expect: AR-CWD-001


def pathlib_methods() -> str:
    Path("out/report.txt").write_text("x", encoding="utf-8")  # expect: AR-CWD-001
    if P("cache").exists():  # expect: AR-CWD-001
        pass
    return Path("templates/base.html").read_text(encoding="utf-8")  # expect: AR-CWD-001


def joined_path() -> bytes:
    return (Path("data") / "blob.bin").read_bytes()  # expect: AR-CWD-001


def open_path_object() -> None:
    open(Path("data") / "x.csv", encoding="utf-8").close()  # expect: AR-CWD-001
    open(os.path.join("data", "y.csv"), encoding="utf-8").close()  # expect: AR-CWD-001


def fstring(name: str) -> None:
    open(f"fixtures/{name}.json", encoding="utf-8").close()  # expect: AR-CWD-001


def os_functions() -> None:
    os.listdir("plugins")  # expect: AR-CWD-001
    os.path.exists("setup.cfg")  # expect: AR-CWD-001
    os.makedirs("build/tmp", exist_ok=True)  # expect: AR-CWD-001
    os.remove("stale.lock")  # expect: AR-CWD-001
    os.chdir("subdir")  # expect: AR-CWD-001


def two_relative_arguments() -> None:
    shutil.copy("src.txt", "dst.txt")  # expect: AR-CWD-001, AR-CWD-001
    os.rename("old.txt", "new.txt")  # expect: AR-CWD-001, AR-CWD-001


def misc() -> None:
    glob.glob("*.py")  # expect: AR-CWD-001
    sqlite3.connect("app.db").close()  # expect: AR-CWD-001
    Path("logs").mkdir(exist_ok=True)  # expect: AR-CWD-001
    for _ in Path("docs").glob("*.md"):  # expect: AR-CWD-001
        pass


class Loader:
    def load(self) -> str:
        return Path("conf.ini").read_text(encoding="utf-8")  # expect: AR-CWD-001


def multi_line() -> None:
    with open(  # expect: AR-CWD-001
        "multi.txt",
        encoding="utf-8",
    ):
        pass


def nested_function() -> None:
    def inner() -> None:
        open("inner.txt", encoding="utf-8").close()  # expect: AR-CWD-001

    inner()


open("module_level.txt", encoding="utf-8").close()  # expect: AR-CWD-001
