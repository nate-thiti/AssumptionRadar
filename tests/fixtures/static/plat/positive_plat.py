"""AR-PLAT-001 positive cases.

Every line with an expect marker comment must produce exactly the listed
findings; unmarked lines must produce none. This file is parsed, never executed,
and is excluded from mypy because it uses APIs of more than one platform.
"""

from __future__ import annotations

import ctypes
import fcntl  # expect: AR-PLAT-001
import os
import shutil
import signal
import subprocess
import sys
import winreg as reg  # expect: AR-PLAT-001
from msvcrt import getch  # expect: AR-PLAT-001
from os import getuid
from pathlib import Path
from resource import getrlimit  # expect: AR-PLAT-001


def windows_paths(name: str) -> None:
    open("C:\\data\\input.csv")  # expect: AR-PLAT-001
    Path("C:/Users/me/config.toml").read_text()  # expect: AR-PLAT-001
    os.listdir("\\\\server\\share")  # expect: AR-PLAT-001
    open("data\\input.csv")  # expect: AR-PLAT-001
    open(os.path.join("logs", "sub\\dir"))  # expect: AR-PLAT-001
    open(f"D:\\exports\\{name}.csv")  # expect: AR-PLAT-001


def posix_paths() -> None:
    open("/etc/hosts")  # expect: AR-PLAT-001
    Path("/tmp/cache").mkdir()  # expect: AR-PLAT-001
    shutil.rmtree("/var/tmp/build")  # expect: AR-PLAT-001
    os.listdir(os.path.join("/opt/app", "x"))  # expect: AR-PLAT-001
    shutil.copy("/etc/a.conf", "C:\\b.conf")  # expect: AR-PLAT-001, AR-PLAT-001


def posix_apis() -> int:
    pid = os.fork()  # expect: AR-PLAT-001
    os.kill(pid, signal.SIGKILL)  # expect: AR-PLAT-001
    return getuid()  # expect: AR-PLAT-001


def windows_apis() -> None:
    os.startfile("report.pdf")  # expect: AR-PLAT-001
    flags = subprocess.CREATE_NO_WINDOW  # expect: AR-PLAT-001
    subprocess.run([sys.executable], creationflags=flags)
    ctypes.windll.kernel32.Beep(750, 300)  # expect: AR-PLAT-001
    print(reg, getch, fcntl, getrlimit)


def guarded_in_a_sibling_function() -> bool:
    return sys.platform == "win32"


def not_guarded_here() -> int:
    return os.getgid()  # expect: AR-PLAT-001


class Daemon:
    def detach(self) -> None:
        os.setsid()  # expect: AR-PLAT-001
