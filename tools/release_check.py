"""Local release check (P1-18): build, inspect, install in clean venvs, smoke-test.

Usage (from the project root):

    python tools/release_check.py [--keep]

Steps, each reported PASS/FAIL; exit status 0 only when all pass:

1. Build sdist and wheel with ``python -m build`` into a temporary directory.
2. ``twine check --strict`` both files.
3. Inspect the wheel: required files and entry points present, no tests/tools.
4. README links are absolute (PyPI renders the README without the repository).
5. Install the wheel (+ pytest) in a fresh venv and smoke-test the CLI and the
   pytest plugin on a small project with a known assumption.
6. Install the sdist in a second fresh venv (builds it from source) and check
   the version and the packaged schema.

Network access is needed to install pytest and the build backend into the
fresh virtual environments. Nothing is uploaded anywhere.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SETTINGS_TEST = """\
import json


def test_settings():
    with open("settings.json", encoding="utf-8") as fh:
        assert json.load(fh)["debug"] is False
"""


class CheckError(Exception):
    """A release check step failed; the message says why."""


def run(argv: Sequence[str | Path], *, cwd: Path | None = None, ok: Sequence[int] = (0,)) -> str:
    """Run a command and return its combined output; raise CheckError on an unexpected exit."""
    result = subprocess.run(
        [str(a) for a in argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode not in ok:
        raise CheckError(f"{' '.join(map(str, argv))} exited {result.returncode}\n{output[-3000:]}")
    return output


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def version() -> str:
    text = (ROOT / "src/assumptionradar/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    if match is None:
        raise CheckError("__version__ not found")
    return match.group(1)


def step_build(work: Path) -> tuple[Path, Path]:
    dist = work / "dist"
    run([sys.executable, "-m", "build", "--outdir", dist, ROOT])
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise CheckError(f"expected one wheel and one sdist, got {sorted(p.name for p in dist.iterdir())}")
    expected = f"assumptionradar-{version()}"
    if not wheels[0].name.startswith(expected) or not sdists[0].name.startswith(expected):
        raise CheckError(f"file names do not match version {version()}: {wheels[0].name}, {sdists[0].name}")
    return wheels[0], sdists[0]


def step_twine(wheel: Path, sdist: Path) -> None:
    run([sys.executable, "-m", "twine", "check", "--strict", wheel, sdist])


def step_inspect_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        entry_points = next(
            zf.read(n).decode() for n in names if n.endswith(".dist-info/entry_points.txt")
        )
        metadata = next(zf.read(n).decode() for n in names if n.endswith(".dist-info/METADATA"))
    required = {
        "assumptionradar/__init__.py",
        "assumptionradar/py.typed",
        "assumptionradar/reporting/schemas/report-v1.json",
        "assumptionradar/integrations/pytest_plugin.py",
        "assumptionradar/analysis/_child.py",
    }
    missing = required - names
    if missing:
        raise CheckError(f"wheel is missing {sorted(missing)}")
    stray = sorted(n for n in names if n.startswith(("tests/", "tools/", "docs/")))
    if stray:
        raise CheckError(f"wheel contains non-package files: {stray[:5]}")
    for needed in (
        "assumptionradar = assumptionradar.cli:main",
        "assumptionradar = assumptionradar.integrations.pytest_plugin",
    ):
        if needed not in entry_points:
            raise CheckError(f"entry point missing: {needed}\n{entry_points}")
    if "License-Expression: MIT" not in metadata:
        raise CheckError("METADATA has no License-Expression: MIT")
    if re.search(r"^Requires-Dist:(?!.*extra ==)", metadata, re.MULTILINE):
        raise CheckError("wheel declares runtime dependencies; v0.1 must have none")


def step_readme_links() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    relative = [
        target
        for target in re.findall(r"\]\(([^)\s]+)\)", text)
        if "://" not in target and not target.startswith(("#", "mailto:"))
    ]
    if relative:
        raise CheckError(f"README has relative links that break on PyPI: {relative}")


def step_wheel_venv(work: Path, wheel: Path) -> None:
    venv = work / "venv-wheel"
    run([sys.executable, "-m", "venv", venv])
    py = venv_python(venv)
    run([py, "-m", "pip", "install", "-q", wheel, "pytest"])

    out = run([py, "-m", "assumptionradar", "--version"]).strip()
    if out != f"assumptionradar {version()}":
        raise CheckError(f"--version printed {out!r}")
    scenarios = json.loads(run([py, "-m", "assumptionradar", "scenarios", "--format", "json"]))
    if [s["family"] for s in scenarios["scenarios"]][:1] != ["cwd"]:
        raise CheckError(f"unexpected scenarios output: {scenarios}")

    project = work / "project"
    (project / "tests").mkdir(parents=True)
    (project / "settings.json").write_text('{"debug": false}\n', encoding="utf-8")
    (project / "tests" / "test_settings.py").write_text(SETTINGS_TEST, encoding="utf-8")

    plain = run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=project)
    if "assumptionradar" in plain.split("passed")[-1] or "1 passed" not in plain:
        raise CheckError(f"plain pytest run is not unaffected:\n{plain}")
    help_text = run([py, "-m", "pytest", "--help"], cwd=project)
    if "--assumption-verify" not in help_text:
        raise CheckError("plugin options missing from pytest --help")
    strict = run(
        [
            py, "-m", "pytest", "-p", "no:cacheprovider", "--assumption-observe",
            "--assumption-verify=cwd", "--assumption-strict", "--assumption-report=r.json",
        ],
        cwd=project,
        ok=(1,),
    )
    if "REGRESSION    tests/test_settings.py::test_settings" not in strict:
        raise CheckError(f"plugin did not report the regression:\n{strict}")
    shown = run([py, "-m", "assumptionradar", "report", "show", "r.json"], cwd=project, ok=(1,))
    if "REGRESSION" not in shown:
        raise CheckError(f"report show output unexpected:\n{shown}")


def step_sdist_venv(work: Path, sdist: Path) -> None:
    venv = work / "venv-sdist"
    run([sys.executable, "-m", "venv", venv])
    py = venv_python(venv)
    run([py, "-m", "pip", "install", "-q", sdist])
    code = (
        "import assumptionradar as a; "
        "from assumptionradar.reporting.json_report import load_report_schema as s; "
        "print(a.__version__, s()['properties']['schema_version']['const'])"
    )
    out = run([py, "-c", code]).strip()
    if out != f"{version()} 1":
        raise CheckError(f"sdist install reported {out!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the work directory")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="ar-release-"))
    results: list[tuple[str, bool, str]] = []
    artifacts: dict[str, Path] = {}

    def attempt(name: str, fn: Callable[[], object]) -> bool:
        try:
            fn()
        except CheckError as exc:
            results.append((name, False, str(exc)))
            return False
        results.append((name, True, ""))
        return True

    def build() -> None:
        artifacts["wheel"], artifacts["sdist"] = step_build(work)

    print(f"assumptionradar {version()} release check; work dir {work}")
    if attempt("build sdist and wheel", build):
        attempt("twine check --strict", lambda: step_twine(artifacts["wheel"], artifacts["sdist"]))
        attempt("wheel contents and metadata", lambda: step_inspect_wheel(artifacts["wheel"]))
        attempt("README links absolute", step_readme_links)
        attempt("wheel venv: CLI + plugin smoke", lambda: step_wheel_venv(work, artifacts["wheel"]))
        attempt("sdist venv: build from source", lambda: step_sdist_venv(work, artifacts["sdist"]))

    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
        if detail:
            print("      " + detail.replace("\n", "\n      "))
    if "wheel" in artifacts:
        print(f"wheel: {artifacts['wheel'].name}  sdist: {artifacts['sdist'].name}")
    if args.keep:
        print(f"kept {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if results and all(passed for _, passed, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
