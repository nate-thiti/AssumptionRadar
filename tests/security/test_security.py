"""Security tests through the real pytest plugin (P1-16).

- Secret values never reach the JSON report or the terminal summary, even when
  the test under analysis puts them in file paths, output or assertion messages.
- The home directory is replaced by ``~`` everywhere in the report.
- Running the plugin leaves the parent process state as it was.
- The per-run timeout stops a hung child, and the child process is gone afterwards.
- Captured child output in the report is bounded.
"""

from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from assumptionradar.analysis import runtime

# Fake secret values used to detect leaks.
TOKEN = "s3cr3t-token-value-0001"  # noqa: S105
PASSWORD = "hunter2-db-pass-0002"  # noqa: S105
URL_PASSWORD = "urlpass-0003"  # noqa: S105

LEAKY = f"""
import os, subprocess, sys, tempfile
from pathlib import Path

def test_leaky():
    token = os.environ["AR_SEC_API_TOKEN"]
    password = os.environ["AR_SEC_DB_PASSWORD"]
    cache = Path(tempfile.gettempdir()) / f"cache-{{token}}.json"
    cache.write_text("{{}}", encoding="utf-8")
    cache.unlink()
    try:
        open(Path.home() / ".ar-security-probe-missing", encoding="utf-8").close()
    except OSError:
        pass
    subprocess.run(
        [sys.executable, "-c", "pass", f"--token={{token}}",
         "https://ci:{URL_PASSWORD}@example.test/"],
        check=True,
    )
    print(f"Authorization: Bearer {{token}}")
    print(f"connecting with {{password}}")
    print("https://ci:{URL_PASSWORD}@example.test/")
    assert Path("data.txt").exists(), f"config missing; token was {{token}}"
"""


def strings(value: object) -> Iterator[str]:
    """Every string (keys and values) inside a JSON document."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def summary_section(output: str) -> str:
    """The part of pytest's output printed by AssumptionRadar."""
    marker = output.find(" assumptionradar ")
    assert marker != -1, output
    return output[marker:]


# ------------------------------------------------------------------- secrets


def test_secret_values_never_reach_report_or_summary(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AR_SEC_API_TOKEN", TOKEN)
    monkeypatch.setenv("AR_SEC_DB_PASSWORD", PASSWORD)
    pytester.makefile(".txt", data="ok")
    pytester.makepyfile(test_leaky=LEAKY)
    result = pytester.runpytest_subprocess(
        "--assumption-observe",
        "--assumption-env-reads",
        "--assumption-verify=cwd",
        "--assumption-report=out.json",
    )
    assert result.ret == 0
    report = load(pytester.path / "out.json")

    # The scenario run failed with the secrets in its output, so there is
    # something to redact: make sure the test exercised that path.
    run = report["verifications"][0]["scenarios"][0]["run"]
    assert report["verifications"][0]["status"] == "regression"
    assert "config missing" in run["output_tail"]

    texts = list(strings(report))
    section = summary_section(result.stdout.str())
    for secret in (TOKEN, PASSWORD, URL_PASSWORD):
        assert not [t for t in texts if secret in t], f"{secret} leaked into the report"
        assert secret not in section, f"{secret} leaked into the terminal summary"

    # Names of variables are kept; their values are not.
    reads = {e["target"] for e in report["observation"]["events"] if e["kind"] == "env_read"}
    assert {"AR_SEC_API_TOKEN", "AR_SEC_DB_PASSWORD"} <= reads


def test_home_directory_is_never_in_report(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AR_SEC_API_TOKEN", TOKEN)
    monkeypatch.setenv("AR_SEC_DB_PASSWORD", PASSWORD)
    pytester.makefile(".txt", data="ok")
    pytester.makepyfile(test_leaky=LEAKY)
    pytester.runpytest_subprocess(
        "--assumption-observe", "--assumption-verify=cwd", "--assumption-report=out.json"
    )
    report = load(pytester.path / "out.json")
    home = str(Path.home()).lower()
    forms = {home, home.replace("\\", "/")}
    leaked = [t for t in strings(report) if any(form in t.lower() for form in forms)]
    assert leaked == []
    targets = [e["target"] for e in report["observation"]["events"]]
    assert "~/.ar-security-probe-missing" in [t.replace("\\", "/") for t in targets]


# -------------------------------------------------------------- parent state


def test_parent_process_state_is_unchanged(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AR_SEC_PRESENT", "value-present")
    pytester.makefile(".txt", data="ok")
    pytester.makepyfile(
        test_state="""
        import os

        def test_it():
            assert os.environ.get("AR_SEC_PRESENT")
            with open("data.txt", encoding="utf-8") as fh:
                assert fh.read() == "ok"
        """
    )
    # Control: a nested in-process pytest run changes some state by itself (it
    # removes PYTEST_CURRENT_TEST). Compare against a run without the plugin's
    # options, so only changes made by AssumptionRadar count.
    assert pytester.runpytest().ret == 0
    before = {
        "environ": dict(os.environ),
        "environ_type": type(os.environ),
        "getitem": os._Environ.__getitem__,
        "cwd": Path.cwd(),
        "tzname": time.tzname,
        "timezone": time.timezone,
        "locale": locale.setlocale(locale.LC_ALL),
        "tempdir": tempfile.tempdir,
        "gettempdir": tempfile.gettempdir(),
    }
    result = pytester.runpytest(
        "--assumption-observe",
        "--assumption-env-reads",
        "--assumption-verify=cwd,tz,encoding,tmpdir,env-unset:AR_SEC_PRESENT",
        "--assumption-report=out.json",
    )
    assert result.ret == 0
    after = {
        "environ": dict(os.environ),
        "environ_type": type(os.environ),
        "getitem": os._Environ.__getitem__,
        "cwd": Path.cwd(),
        "tzname": time.tzname,
        "timezone": time.timezone,
        "locale": locale.setlocale(locale.LC_ALL),
        "tempdir": tempfile.tempdir,
        "gettempdir": tempfile.gettempdir(),
    }
    env_before: dict[str, str] = before.pop("environ")  # type: ignore[assignment]
    env_after: dict[str, str] = after.pop("environ")  # type: ignore[assignment]
    changed = {
        key: (env_before.get(key), env_after.get(key))
        for key in env_before.keys() | env_after.keys()
        if env_before.get(key) != env_after.get(key)
    }
    assert changed == {}
    assert after == before
    assert runtime._ACTIVE is None
    statuses = {v["status"] for v in load(pytester.path / "out.json")["verifications"]}
    assert statuses == {"regression"}  # the scenarios really ran in children


# ------------------------------------------------------------------- timeout


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],  # noqa: S607 - system tool
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_timeout_stops_hung_child(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_hang="""
        import os, time
        from pathlib import Path

        HERE = Path(__file__).resolve().parent

        def test_hangs_outside_project_dir():
            if Path.cwd().resolve() != HERE:
                (HERE / "child.pid").write_text(str(os.getpid()), encoding="utf-8")
                time.sleep(120)
        """
    )
    start = time.monotonic()
    result = pytester.runpytest_subprocess(
        "--assumption-verify=cwd", "--assumption-timeout=3", "--assumption-report=out.json"
    )
    elapsed = time.monotonic() - start
    assert result.ret == 0
    assert elapsed < 60, f"took {elapsed:.0f}s; the 120s sleep was not cut off"

    scenario = load(pytester.path / "out.json")["verifications"][0]["scenarios"][0]
    assert scenario["status"] == "inconclusive"
    assert scenario["run"]["outcome"] == "timeout"
    assert scenario["run"]["exit_code"] is None

    pid = int((pytester.path / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(pid), f"hung test process {pid} is still running after the timeout"


# --------------------------------------------------------------- output size


def test_child_output_in_report_is_bounded(pytester: pytest.Pytester) -> None:
    pytester.makefile(".txt", data="ok")
    pytester.makepyfile(
        test_noisy="""
        def test_noisy():
            print("x" * 200_000)
            with open("data.txt", encoding="utf-8") as fh:
                assert fh.read() == "ok"
        """
    )
    pytester.runpytest_subprocess("--assumption-verify=cwd", "--assumption-report=out.json")
    path = pytester.path / "out.json"
    report = load(path)
    runs = [*report["verifications"][0]["baseline"]]
    runs += [s["run"] for s in report["verifications"][0]["scenarios"]]
    assert report["verifications"][0]["status"] == "regression"
    assert all(len(run["output_tail"]) <= 4000 for run in runs)
    assert path.stat().st_size < 50_000
