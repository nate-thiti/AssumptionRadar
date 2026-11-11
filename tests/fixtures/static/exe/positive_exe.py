"""AR-EXE-001 positive cases.

Every line with an expect marker comment must produce exactly the listed
findings; unmarked lines must produce none. This file is parsed, never executed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import subprocess as sp
from subprocess import Popen, check_output


def list_form() -> None:
    subprocess.run(["git", "status"], check=True)  # expect: AR-EXE-001


def tuple_form() -> bytes:
    return check_output(("docker", "ps"))  # expect: AR-EXE-001


def aliases() -> None:
    Popen(["node", "--version"]).wait()  # expect: AR-EXE-001
    sp.call(["make"])  # expect: AR-EXE-001


def keyword_args() -> None:
    subprocess.run(args=["terraform", "plan"], check=False)  # expect: AR-EXE-001


def built_lists(extra: list[str]) -> None:
    subprocess.run(["pytest", *extra])  # expect: AR-EXE-001
    subprocess.run(["ruff", "check", *extra] + extra)  # expect: AR-EXE-001


def string_commands() -> None:
    subprocess.run("ffmpeg -version", shell=True)  # expect: AR-EXE-001
    os.system("convert in.png out.jpg")  # expect: AR-EXE-001
    os.popen("whoami").read()  # expect: AR-EXE-001
    subprocess.getoutput("uname -a")  # expect: AR-EXE-001
    subprocess.run("FOO=1 BAR=2 java -version", shell=True)  # expect: AR-EXE-001
    subprocess.run("hostname")  # expect: AR-EXE-001


def exec_family() -> None:
    os.execvp("bash", ["bash"])  # expect: AR-EXE-001
    os.execlp("sh", "sh")  # expect: AR-EXE-001


async def asyncio_family() -> None:
    await asyncio.create_subprocess_exec("redis-cli", "ping")  # expect: AR-EXE-001
    await asyncio.create_subprocess_shell("kubectl get pods")  # expect: AR-EXE-001


def which_checks_another_tool() -> None:
    if shutil.which("git"):
        subprocess.run(["hg", "status"])  # expect: AR-EXE-001


def which_in_a_sibling_function() -> bool:
    return shutil.which("svn") is not None


def not_guarded_here() -> None:
    subprocess.run(["svn", "info"])  # expect: AR-EXE-001


class Tool:
    def version(self) -> str:
        return subprocess.check_output(["gcc", "--version"], text=True)  # expect: AR-EXE-001


subprocess.run(["python", "-V"])  # expect: AR-EXE-001
