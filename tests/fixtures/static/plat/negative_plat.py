"""AR-PLAT-001 negative cases: nothing in this file may be reported.

This file is parsed, never executed, and is excluded from mypy.
"""

from __future__ import annotations

import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import winreg

try:
    import fcntl
except ImportError:
    fcntl = None


def portable_paths(tmp_path: Path) -> None:
    open(os.path.join("data", "input.csv"))
    (tmp_path / "x").write_text("x")
    print(Path(tempfile.gettempdir()) / "x")
    open(os.devnull)
    Path("relative/forward/slashes.txt").exists()


def explicit_flavours() -> None:
    print(PureWindowsPath("C:\\Users\\me"), PurePosixPath("/etc/hosts"))


def paths_built_but_not_accessed() -> None:
    print(Path("/home/bob"), os.path.join("C:\\", "x"), str(Path("/etc") / "hosts"))


def strings_that_are_not_path_arguments() -> None:
    print("C:\\not\\a\\path\\argument")
    print("https://example.com/a/b", "a/b".split("/"))
    re.compile("\\d+")


def guarded_by_if() -> None:
    if sys.platform == "win32":
        os.startfile("x.pdf")
    else:
        os.fork()


def guarded_by_os_name() -> None:
    if os.name == "nt":
        open("C:\\x.txt")


def guarded_by_platform_system() -> None:
    if platform.system() == "Linux":
        open("/proc/cpuinfo")


def guarded_by_hasattr() -> None:
    if hasattr(os, "getuid"):
        os.getuid()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def guarded_by_skipif() -> None:
    os.kill(os.getpid(), signal.SIGUSR1)


def guarded_by_early_return() -> None:
    if os.name != "posix":
        return
    os.chown("/tmp/x", 0, 0)


def guarded_by_try() -> None:
    try:
        os.getloadavg()
    except (AttributeError, OSError):
        pass


def guarded_nested() -> None:
    if sys.platform.startswith("linux"):

        def inner() -> None:
            open("/proc/meminfo")

        inner()


def cross_platform_apis() -> None:
    os.kill(os.getpid(), signal.SIGTERM)
    print(signal.SIGINT, os.sep, os.pathsep, os.linesep, winreg, fcntl)
    subprocess.run([sys.executable, "-V"])
