"""Tests for parsing, the per-file cache and the scan engine (P2-1)."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable
from pathlib import Path

import pytest

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import (
    Confidence,
    Evidence,
    EvidenceKind,
    Finding,
    Severity,
    SourceLocation,
)
from assumptionradar.static.cache import FileCache
from assumptionradar.static.engine import ENGINE_VERSION, ScanResult, scan
from assumptionradar.static.parsing import ParsedModule, ScanError, parse_file


def write(root: Path, rel: str, content: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------- parsing


def test_parse_ok(tmp_path: Path) -> None:
    path = write(tmp_path, "pkg/m.py", "from pathlib import Path as P\nx = P('a')\n")
    module = parse_file(path, tmp_path)
    assert isinstance(module, ParsedModule)
    assert module.relpath == "pkg/m.py"
    assert module.source.startswith("from pathlib")
    assert module.source_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert isinstance(module.tree, ast.Module)
    assert module.imports.names["P"] == "pathlib.Path"


def test_parse_syntax_error_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path, "bad.py", "x = 1\ndef f(:\n    pass\n")
    error = parse_file(path, tmp_path)
    assert isinstance(error, ScanError)
    assert (error.relpath, error.kind, error.line) == ("bad.py", "syntax", 2)
    assert error.message


def test_parse_honours_encoding_cookie_and_bom(tmp_path: Path) -> None:
    latin = write(
        tmp_path, "latin.py", "# -*- coding: latin-1 -*-\ns = 'caf\xe9'\n".encode("latin-1")
    )
    bom = write(tmp_path, "bom.py", b"\xef\xbb\xbfs = '\xe0\xb8\x81'\n")
    m1 = parse_file(latin, tmp_path)
    m2 = parse_file(bom, tmp_path)
    assert isinstance(m1, ParsedModule)
    assert "café" in m1.source
    assert isinstance(m2, ParsedModule)
    assert "ก" in m2.source


def test_parse_undecodable_file_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path, "bin.py", b"s = '\xff\xfe\xfa'\n")
    error = parse_file(path, tmp_path)
    assert isinstance(error, ScanError)
    assert error.kind == "decode"
    assert error.line is None


def test_parse_null_bytes_are_reported_not_raised(tmp_path: Path) -> None:
    path = write(tmp_path, "nul.py", b"x = 1\x00\n")
    error = parse_file(path, tmp_path)
    assert isinstance(error, ScanError)
    assert error.kind == "syntax"


def test_scanned_code_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "EXECUTED"
    evil = f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n"
    write(tmp_path, "evil.py", evil + "import os\nos._exit(3)\n")
    write(tmp_path, "conftest.py", f"open({str(marker)!r}, 'w').close()\nraise SystemExit(4)\n")
    result = scan(tmp_path, [])
    assert result.files_scanned == 2
    assert not marker.exists()


# ----------------------------------------------------------------------- cache


def test_cache_round_trip_and_counters(tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "c", "ns1")
    assert cache.get("ab" * 32) is None
    cache.put("ab" * 32, {"findings": [1, 2]})
    assert cache.get("ab" * 32) == {"findings": [1, 2]}
    assert (cache.hits, cache.misses) == (1, 1)
    assert FileCache(tmp_path / "c", "ns2").get("ab" * 32) is None  # namespaces are separate


def test_cache_corrupt_entry_is_a_miss(tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "c", "ns")
    cache.put("cd" * 32, {"a": 1})
    entry = next((tmp_path / "c").rglob("*.json"))
    entry.write_text("{not json", encoding="utf-8")
    assert cache.get("cd" * 32) is None
    assert cache.misses == 1


def test_cache_rejects_bad_keys(tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "c", "ns")
    for bad in ("../x", "short", "Z" * 64):
        with pytest.raises(ValueError, match="key"):
            cache.put(bad, {})


# ---------------------------------------------------------------------- engine


class OpenCallRule:
    """Test rule: one finding per call to the builtin open()."""

    rule_id = "AR-TST-001"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def check(self, module: ParsedModule) -> Iterable[Finding]:
        self.calls += 1
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call) and module.imports.resolve(node.func) == "builtins.open":
                yield Finding(
                    rule_id=self.rule_id,
                    title="open() call",
                    message="open() is called",
                    category="filesystem",
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    fingerprint=compute_fingerprint(self.rule_id, module.relpath, None, "open"),
                    location=SourceLocation(module.relpath, node.lineno, node.col_offset + 1),
                    evidence=(Evidence(EvidenceKind.INFERRED, "ast", "open() call"),),
                    metadata={"k": "v"},
                )


class CrashingRule:
    rule_id = "AR-TST-002"
    version = "1"

    def check(self, module: ParsedModule) -> Iterable[Finding]:
        raise RuntimeError("boom")


def test_scan_collects_sorted_disambiguated_findings(tmp_path: Path) -> None:
    write(tmp_path, "b.py", "open('x')\n")
    write(tmp_path, "a.py", "x = 1\nopen('y')\nopen('z')\n")
    result = scan(tmp_path, [OpenCallRule()])
    assert isinstance(result, ScanResult)
    assert [(f.location.path, f.location.line) for f in result.findings if f.location] == [
        ("a.py", 2),
        ("a.py", 3),
        ("b.py", 1),
    ]
    fps = [f.fingerprint for f in result.findings]
    assert fps[1] == fps[0] + ":2"  # same key in the same file: disambiguated
    assert result.errors == ()
    assert result.files_scanned == 2


def test_scan_reports_syntax_errors_and_continues(tmp_path: Path) -> None:
    write(tmp_path, "bad.py", "def f(:\n")
    write(tmp_path, "good.py", "open('x')\n")
    result = scan(tmp_path, [OpenCallRule()])
    assert [e.relpath for e in result.errors] == ["bad.py"]
    assert [f.location.path for f in result.findings if f.location] == ["good.py"]
    assert result.files_scanned == 1


def test_crashing_rule_is_isolated(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "open('x')\n")
    result = scan(tmp_path, [CrashingRule(), OpenCallRule()])
    assert len(result.findings) == 1
    assert [(e.relpath, e.kind) for e in result.errors] == [("a.py", "rule")]
    assert "AR-TST-002" in result.errors[0].message
    assert "boom" in result.errors[0].message


def test_skipped_files_are_reported(tmp_path: Path) -> None:
    write(tmp_path, "big.py", "x = 1\n" * 100)
    result = scan(tmp_path, [], max_file_bytes=50)
    assert [s.path.name for s in result.skipped] == ["big.py"]


def test_scan_uses_cache(tmp_path: Path) -> None:
    project = tmp_path / "p"
    write(project, "a.py", "open('x')\n")
    write(project, "b.py", "x = 1\n")
    cache_dir = tmp_path / "cache"

    rule = OpenCallRule()
    first = scan(project, [rule], cache=FileCache(cache_dir, "test"))
    assert (first.cache_hits, first.cache_misses, rule.calls) == (0, 2, 2)

    rule2 = OpenCallRule()
    second = scan(project, [rule2], cache=FileCache(cache_dir, "test"))
    assert (second.cache_hits, second.cache_misses, rule2.calls) == (2, 0, 0)
    assert second.findings == first.findings  # Finding.from_dict round-trips exactly

    write(project, "b.py", "open('changed')\n")
    rule3 = OpenCallRule()
    third = scan(project, [rule3], cache=FileCache(cache_dir, "test"))
    assert (third.cache_hits, third.cache_misses, rule3.calls) == (1, 1, 1)
    assert len(third.findings) == 2


def test_cache_key_depends_on_rule_versions(tmp_path: Path) -> None:
    project = tmp_path / "p"
    write(project, "a.py", "open('x')\n")
    cache_dir = tmp_path / "cache"
    scan(project, [OpenCallRule()], cache=FileCache(cache_dir, "test"))
    newer = OpenCallRule()
    newer.version = "2"
    result = scan(project, [newer], cache=FileCache(cache_dir, "test"))
    assert (result.cache_hits, newer.calls) == (0, 1)


def test_engine_version_is_a_string() -> None:
    assert isinstance(ENGINE_VERSION, str)
    assert ENGINE_VERSION


def test_finding_from_dict_round_trip() -> None:
    finding = Finding(
        rule_id="AR-TST-001",
        title="t",
        message="m",
        category="cwd",
        severity=Severity.WARNING,
        confidence=Confidence.MEDIUM,
        fingerprint="0123456789abcdef:2",
        location=SourceLocation("a/b.py", 3, 4, "f"),
        evidence=(Evidence(EvidenceKind.INFERRED, "ast", "d", SourceLocation("a/b.py", 3)),),
        remediation="r",
        failure_scenario="fs",
        docs_url="https://example.test/x",
        test_id="t.py::t",
        metadata={"a": "1"},
    )
    assert Finding.from_dict(finding.to_dict()) == finding
    bare = Finding("AR-TST-001", "t", "m", "cwd", Severity.INFO, Confidence.LOW, "0123456789abcdef")
    assert Finding.from_dict(bare.to_dict()) == bare
