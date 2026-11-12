"""Tests for ``assumptionradar baseline create`` / ``baseline compare`` (P2-9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assumptionradar.baseline import load_baseline
from assumptionradar.cli import ExitCode, main

DEFAULT_NAME = ".assumptionradar-baseline.json"
TWO = "open('a.json')\nopen('b.json')\n"


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def project(tmp_path: Path, source: str = TWO, config: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "m.py").write_text(source, encoding="utf-8")
    if config is not None:
        (root / "assumptionradar.toml").write_text(config, encoding="utf-8")
    return root


def create(root: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    code, _, _ = run(["baseline", "create", str(root)], capsys)
    assert code == ExitCode.OK
    return root / DEFAULT_NAME


# ---------------------------------------------------------------------- create


def test_create_writes_default_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code, out, err = run(["baseline", "create", str(root)], capsys)
    path = root / DEFAULT_NAME
    assert code == ExitCode.OK
    assert out == f"baseline written: {path} (2 findings)\n"
    assert err == ""
    assert [e.rule_id for e in load_baseline(path).entries] == ["AR-CWD-001", "AR-CWD-001"]


def test_create_singular_and_custom_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "open('a.json')\n")
    target = tmp_path / "custom.json"
    code, out, _ = run(["baseline", "create", str(root), "--output", str(target)], capsys)
    assert (code, out) == (ExitCode.OK, f"baseline written: {target} (1 finding)\n")
    assert target.is_file()
    assert not (root / DEFAULT_NAME).exists()


def test_create_uses_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, config='ignore-rules = ["AR-CWD-001"]\ncache = false\n')
    code, out, _ = run(["baseline", "create", str(root)], capsys)
    assert code == ExitCode.OK
    assert out.endswith("(0 findings)\n")
    assert not (root / ".assumptionradar_cache").exists()


def test_create_reports_unscannable_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    (root / "bad.py").write_text("def f(:\n", encoding="utf-8")
    code, out, err = run(["baseline", "create", str(root)], capsys)
    assert code == ExitCode.OK
    assert out.endswith("(2 findings)\n")
    assert err.startswith("assumptionradar: warning: bad.py could not be scanned (syntax): ")
    assert err.endswith("\n")


def test_create_with_bad_config_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, config='fail-on = "loud"\n')
    code, out, err = run(["baseline", "create", str(root)], capsys)
    assert code == ExitCode.USAGE
    assert out == ""
    assert err.startswith(f"assumptionradar: {root / 'assumptionradar.toml'}: ")
    assert not (root / DEFAULT_NAME).exists()


def test_create_missing_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["baseline", "create", str(tmp_path / "nope")], capsys)
    assert code == ExitCode.USAGE
    assert "is not a directory" in err


# --------------------------------------------------------------------- compare


def test_compare_without_changes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    path = create(root, capsys)
    code, out, err = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.OK
    assert out == f"baseline {path}: 0 new, 2 unchanged, 0 resolved\n"
    assert err == ""


def test_compare_with_a_new_finding_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    path = create(root, capsys)
    (root / "m.py").write_text(TWO + "open('c.json')\n", encoding="utf-8")
    code, out, _ = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.FINDINGS
    lines = out.splitlines()
    assert lines[0] == f"baseline {path}: 1 new, 2 unchanged, 0 resolved"
    assert len(lines) == 2
    parts = lines[1].split()
    assert parts[:3] == ["new", "AR-CWD-001", "m.py:3"]
    assert "c.json" in lines[1]


def test_compare_with_a_resolved_finding_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    create(root, capsys)
    (root / "m.py").write_text("open('a.json')\n", encoding="utf-8")
    code, out, _ = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.OK
    lines = out.splitlines()
    assert lines[0].endswith(": 0 new, 1 unchanged, 1 resolved")
    assert lines[1].split()[:3] == ["resolved", "AR-CWD-001", "m.py"]
    assert "b.json" in lines[1]


def test_new_findings_are_listed_before_resolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    create(root, capsys)
    (root / "m.py").write_text("open('x.json')\n", encoding="utf-8")
    _, out, _ = run(["baseline", "compare", str(root)], capsys)
    kinds = [line.split()[0] for line in out.splitlines()[1:]]
    assert kinds == ["new", "resolved", "resolved"]


def test_fail_on_option_and_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    create(root, capsys)
    (root / "m.py").write_text(TWO + "open('c.json')\n", encoding="utf-8")  # a new warning
    code, _, _ = run(["baseline", "compare", str(root), "--fail-on", "error"], capsys)
    assert code == ExitCode.OK

    (root / "assumptionradar.toml").write_text('fail-on = "error"\n', encoding="utf-8")
    code, _, _ = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.OK
    code, _, _ = run(["baseline", "compare", str(root), "--fail-on", "warning"], capsys)
    assert code == ExitCode.FINDINGS


def test_info_findings_fail_only_with_fail_on_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "x = 1\n", config="[env]\ndeclared = []\n")
    create(root, capsys)
    (root / "m.py").write_text("import os\nos.environ.get('TOKEN')\n", encoding="utf-8")
    code, out, _ = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.OK
    assert ": 1 new," in out
    code, _, _ = run(["baseline", "compare", str(root), "--fail-on", "info"], capsys)
    assert code == ExitCode.FINDINGS


def test_compare_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    path = create(root, capsys)
    (root / "m.py").write_text("open('a.json')\nopen('c.json')\n", encoding="utf-8")
    code, out, _ = run(["baseline", "compare", str(root), "--format", "json"], capsys)
    assert code == ExitCode.FINDINGS
    data = json.loads(out)
    assert list(data) == ["baseline", "summary", "new", "unchanged", "resolved"]
    assert data["baseline"] == str(path)
    assert data["summary"] == {"new": 1, "unchanged": 1, "resolved": 1}
    assert data["new"][0]["rule_id"] == "AR-CWD-001"
    assert data["new"][0]["location"]["line"] == 2
    assert data["resolved"][0]["path"] == "m.py"


def test_compare_with_explicit_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    target = tmp_path / "custom.json"
    run(["baseline", "create", str(root), "--output", str(target)], capsys)
    code, out, _ = run(["baseline", "compare", str(root), "--baseline", str(target)], capsys)
    assert code == ExitCode.OK
    assert out.startswith(f"baseline {target}: 0 new, 2 unchanged")


def test_compare_without_a_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code, out, err = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.USAGE
    assert out == ""
    assert err == (
        f"assumptionradar: {root / DEFAULT_NAME}: baseline not found; "
        "run 'assumptionradar baseline create' first\n"
    )


def test_compare_with_a_broken_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    (root / DEFAULT_NAME).write_text("[]", encoding="utf-8")
    code, _, err = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.USAGE
    assert "not an AssumptionRadar baseline" in err
    assert "Traceback" not in err


def test_compare_reports_unscannable_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    create(root, capsys)
    (root / "bad.py").write_text("def f(:\n", encoding="utf-8")
    code, _, err = run(["baseline", "compare", str(root)], capsys)
    assert code == ExitCode.OK
    assert "bad.py could not be scanned (syntax)" in err


def test_bad_fail_on_value_is_rejected_by_argparse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    code, _, _ = run(["baseline", "compare", str(root), "--fail-on", "loud"], capsys)
    assert code == ExitCode.USAGE
