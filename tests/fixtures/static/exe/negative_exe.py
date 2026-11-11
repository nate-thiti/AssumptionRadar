"""AR-EXE-001 negative cases: nothing in this file may be reported.

This file is parsed, never executed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

import pytest

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")


def uses_sys_executable() -> None:
    subprocess.run([sys.executable, "-m", "pip", "--version"], check=True)


def absolute_paths() -> None:
    subprocess.run(["/usr/bin/git", "status"])
    subprocess.run(["C:\\Tools\\git.exe", "status"])
    subprocess.run(["C:/Tools/git.exe", "status"])
    subprocess.run("/bin/ls -la", shell=True)


def relative_scripts_are_not_path_lookups() -> None:
    subprocess.run(["./build.sh"])
    subprocess.run(["scripts/build.sh"])
    subprocess.run(["scripts\\build.bat"])


def variable_programs(git: str, cmd: list[str]) -> None:
    subprocess.run([git, "status"])
    subprocess.run(cmd)
    subprocess.run([*cmd, "x"])


def guarded_by_which() -> None:
    if shutil.which("git") is None:
        pytest.skip("git missing")
    subprocess.run(["git", "status"])


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def guarded_by_decorator() -> None:
    subprocess.run(["make"])


def guarded_by_module_mark() -> None:
    subprocess.run(["docker", "ps"])


def explicit_executable() -> None:
    subprocess.run(["git", "status"], executable="/usr/bin/git")


def shell_builtins_and_variables() -> None:
    os.system("cd /tmp")
    subprocess.run("echo hello", shell=True)
    subprocess.run("exit 0", shell=True)
    subprocess.run("$EDITOR notes.txt", shell=True)
    subprocess.run("A=1", shell=True)


def not_subprocess() -> None:
    webbrowser.open("index.html")


def empty_and_dynamic(name: str) -> None:
    subprocess.run([])
    subprocess.run("", shell=True)
    subprocess.run(f"{name} --version", shell=True)
