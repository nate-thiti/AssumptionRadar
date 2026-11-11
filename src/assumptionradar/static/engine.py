"""Scan engine for the static scanner (ADR-0005).

Discovers files, parses them without executing them, runs rules over each
parsed module and returns disambiguated findings together with errors.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from assumptionradar.core.fingerprint import disambiguate
from assumptionradar.core.models import Finding
from assumptionradar.static.cache import FileCache
from assumptionradar.static.discovery import SkippedFile, discover
from assumptionradar.static.parsing import ParsedModule, ScanError, parse_file

ENGINE_VERSION: Final = "1"


class Rule(Protocol):
    """A static rule: produces findings for one parsed module."""

    rule_id: str
    version: str

    def check(self, module: ParsedModule) -> Iterable[Finding]:
        """Return the findings for ``module``."""
        ...


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of one scan.

    Attributes:
        findings: Findings sorted by sort key, fingerprints disambiguated.
        errors: Per-file errors (syntax, decode, read, rule crashes).
        skipped: Files skipped because they exceed the size limit.
        files_scanned: Number of files parsed successfully.
        cache_hits: Cache hits during this scan.
        cache_misses: Cache misses during this scan.
    """

    findings: tuple[Finding, ...]
    errors: tuple[ScanError, ...]
    skipped: tuple[SkippedFile, ...]
    files_scanned: int
    cache_hits: int
    cache_misses: int


def _cache_key(relpath: str, source_hash: str, signature: str) -> str:
    """Return the cache key for one file under one rule signature."""
    payload = f"{ENGINE_VERSION}\0{relpath}\0{source_hash}\0" + signature
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scan(
    root: Path,
    rules: Sequence[Rule],
    *,
    include: Sequence[str] = ("**/*.py",),
    exclude: Sequence[str] = (),
    use_default_excludes: bool = True,
    max_file_bytes: int = 1_000_000,
    cache: FileCache | None = None,
) -> ScanResult:
    """Scan a project root with the given rules without executing any code.

    Args:
        root: Project root directory.
        rules: Rules to run on every parsed file.
        include: Glob patterns of files to scan.
        exclude: Glob patterns of files and directories to ignore.
        use_default_excludes: Also apply the default exclude list.
        max_file_bytes: Files larger than this are skipped.
        cache: Optional per-file result cache.

    Returns:
        The scan result.
    """
    discovery = discover(
        root,
        include=include,
        exclude=exclude,
        use_default_excludes=use_default_excludes,
        max_file_bytes=max_file_bytes,
    )
    signature = "\0".join(sorted(f"{rule.rule_id}@{rule.version}" for rule in rules))
    hits_before = cache.hits if cache is not None else 0
    misses_before = cache.misses if cache is not None else 0

    findings: list[Finding] = []
    errors: list[ScanError] = []
    files_scanned = 0
    for path in discovery.files:
        parsed = parse_file(path, root)
        if isinstance(parsed, ScanError):
            errors.append(parsed)
            continue
        files_scanned += 1

        key = (
            _cache_key(parsed.relpath, parsed.source_hash, signature) if cache is not None else None
        )
        if cache is not None and key is not None:
            entry = cache.get(key)
            if entry is not None:
                try:
                    cached = [Finding.from_dict(item) for item in entry["findings"]]
                except (KeyError, TypeError, ValueError):
                    cached = None
                if cached is not None:
                    findings.extend(cached)
                    continue

        module_findings: list[Finding] = []
        crashed = False
        for rule in rules:
            try:
                module_findings.extend(list(rule.check(parsed)))
            except Exception as exc:
                crashed = True
                errors.append(
                    ScanError(
                        parsed.relpath,
                        "rule",
                        f"{rule.rule_id} crashed: {type(exc).__name__}: {exc}",
                    )
                )
        if cache is not None and key is not None and not crashed:
            cache.put(key, {"findings": [item.to_dict() for item in module_findings]})
        findings.extend(module_findings)

    errors.sort(key=lambda error: (error.relpath, error.kind, error.line or 0))
    return ScanResult(
        findings=disambiguate(findings),
        errors=tuple(errors),
        skipped=discovery.skipped,
        files_scanned=files_scanned,
        cache_hits=(cache.hits - hits_before) if cache is not None else 0,
        cache_misses=(cache.misses - misses_before) if cache is not None else 0,
    )
