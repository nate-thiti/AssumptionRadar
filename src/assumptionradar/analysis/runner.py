"""Run one test in a child pytest process under a scenario (ADR-0003).

The runner never modifies ``os.environ`` or the parent working directory. Each
call uses a fresh temporary directory that holds the probe file and any
isolated working or temporary directories, and is removed afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from assumptionradar.analysis.verification import (
    RunOutcome,
    RunResult,
    Scenario,
    outcome_from_exit_code,
)
from assumptionradar.utils.redact import redact_text

CHILD_CONFIG_ENV = "ASSUMPTIONRADAR_CHILD"
_CHILD_MODULE = "assumptionradar.analysis._child"


def _decode(data: bytes | str | None) -> str:
    """Decode child output as UTF-8, replacing undecodable bytes."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _read_probe(path: Path) -> dict[str, str] | None:
    """Read the child probe file. Return None unless it is a JSON object of strings."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
        return None
    return dict(data)


def _collect_errors(path: Path) -> int:
    """Return the number of collection errors the child reported (0 if unknown)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    value = data.get("collect_errors") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class SubprocessRunner:
    """Runs one test in a child process, optionally under a scenario."""

    def __init__(
        self,
        *,
        python: str | None = None,
        timeout_s: float = 120.0,
        max_output_chars: int = 4000,
        base_env: Mapping[str, str] | None = None,
        extra_pytest_args: Sequence[str] = (),
    ) -> None:
        """Configure the runner.

        Raises:
            ValueError: If ``timeout_s`` is not positive or ``max_output_chars`` is less than 1.
        """
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
        if max_output_chars < 1:
            raise ValueError(f"max_output_chars must be >= 1, got {max_output_chars}")
        self._python = python if python is not None else sys.executable
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars
        self._base_env: dict[str, str] | None = None if base_env is None else dict(base_env)
        self._extra_pytest_args: tuple[str, ...] = tuple(extra_pytest_args)

    def __call__(self, test_id: str, project_root: Path, scenario: Scenario | None) -> RunResult:
        """Run ``test_id`` in a child process under ``scenario`` and classify the result.

        A None scenario is a baseline run.
        """
        with tempfile.TemporaryDirectory(
            prefix="assumptionradar-", ignore_cleanup_errors=True
        ) as work_name:
            work = Path(work_name)
            probe_path = work / "probe.json"
            result_path = work / "result.json"

            isolated_cwd: Path | None = None
            if scenario is not None and scenario.isolated_cwd:
                isolated_cwd = work / "empty cwd"
                isolated_cwd.mkdir()
            isolated_tmp: Path | None = None
            if scenario is not None and scenario.isolated_tmp:
                isolated_tmp = work / "tmp dir ทดสอบ"
                isolated_tmp.mkdir()

            child_cwd = isolated_cwd if isolated_cwd is not None else project_root
            env = self._build_env(scenario, project_root, probe_path, isolated_cwd, isolated_tmp)
            cmd = [
                self._python,
                "-m",
                _CHILD_MODULE,
                "-p",
                "no:cacheprovider",
                "-q",
                "--no-header",
                "-rfE",
                "--tb=short",
                f"--rootdir={project_root}",
                *self._extra_pytest_args,
                self._nodeid(test_id, project_root),
            ]

            start = time.perf_counter()
            try:
                completed = subprocess.run(  # noqa: S603 - argument list, no shell
                    cmd,
                    cwd=child_cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=self._timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                duration = time.perf_counter() - start
                outcome = RunOutcome.TIMEOUT
                exit_code: int | None = None
                raw_out: bytes | str | None = exc.stdout
                raw_err: bytes | str | None = exc.stderr
            else:
                duration = time.perf_counter() - start
                outcome = outcome_from_exit_code(completed.returncode)
                exit_code = completed.returncode
                raw_out = completed.stdout
                raw_err = completed.stderr

            if outcome is not RunOutcome.TIMEOUT and _collect_errors(result_path) > 0:
                outcome = RunOutcome.COLLECTION_ERROR

            full = "\n".join((_decode(raw_out), _decode(raw_err)))
            redacted = redact_text(full, max_length=max(len(full), 1))
            output_tail = redacted[-self._max_output_chars :]
            probe = _read_probe(probe_path)

            return RunResult(outcome, exit_code, duration, output_tail, probe)

    def _build_env(
        self,
        scenario: Scenario | None,
        project_root: Path,
        probe_path: Path,
        isolated_cwd: Path | None,
        isolated_tmp: Path | None,
    ) -> dict[str, str]:
        """Build the child environment for one run."""
        env: dict[str, str] = (
            dict(self._base_env) if self._base_env is not None else dict(os.environ)
        )
        env.pop(CHILD_CONFIG_ENV, None)
        if scenario is not None:
            for name in scenario.env_unset:
                env.pop(name, None)
            env.update(scenario.env_set)
        if isolated_tmp is not None:
            for key in ("TMPDIR", "TEMP", "TMP"):
                env[key] = str(isolated_tmp)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            os.pathsep.join([str(project_root), existing]) if existing else str(project_root)
        )

        child_locale = scenario.child_locale if scenario is not None else None
        config = {
            "probe_path": str(probe_path),
            "result_path": str(probe_path.with_name("result.json")),
            "locale": child_locale or None,
            "isolated_cwd": str(isolated_cwd) if isolated_cwd is not None else None,
            "isolated_tmp": str(isolated_tmp) if isolated_tmp is not None else None,
            "check_env": list(scenario.env_unset) if scenario is not None else [],
        }
        env[CHILD_CONFIG_ENV] = json.dumps(config)
        return env

    @staticmethod
    def _nodeid(test_id: str, project_root: Path) -> str:
        """Convert ``path::name`` into an absolute pytest node id."""
        path_part, sep, rest = test_id.partition("::")
        absolute = str(project_root / path_part)
        return absolute + "::" + rest if sep else absolute
