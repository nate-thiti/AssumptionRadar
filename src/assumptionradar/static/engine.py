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
from assumptionradar.static.suppression import (
    Suppression,
    SuppressionProblem,
    parse_suppressions,
)

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
        findings: Findings not covered by a suppression, sorted by sort key,
            fingerprints disambiguated before suppression was applied.
        errors: Per-file errors (syntax, decode, read, rule crashes).
        skipped: Files skipped because they exceed the size limit.
        files_scanned: Number of files parsed successfully.
        cache_hits: Cache hits during this scan.
        cache_misses: Cache misses during this scan.
        suppressed: Findings removed by inline suppression comments, in the
            same order as ``findings``.
        suppression_problems: Malformed, bare (strict mode) and stale
            suppression comments, sorted by (relpath, line, kind, message).
    """

    findings: tuple[Finding, ...]
    errors: tuple[ScanError, ...]
    skipped: tuple[SkippedFile, ...]
    files_scanned: int
    cache_hits: int
    cache_misses: int
    suppressed: tuple[Finding, ...] = ()
    suppression_problems: tuple[SuppressionProblem, ...] = ()


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
    strict_suppressions: bool = False,
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
        strict_suppressions: When True, bare ``# assumptionradar: ignore``
            comments do not suppress anything and are reported as problems.

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
    file_suppressions: dict[str, tuple[list[Suppression], list[tuple[int, str]]]] = {}
    for path in discovery.files:
        parsed = parse_file(path, root)
        if isinstance(parsed, ScanError):
            errors.append(parsed)
            continue
        files_scanned += 1
        file_suppressions[parsed.relpath] = parse_suppressions(parsed.source)

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

    # Fingerprints are assigned before suppression so they never depend on it.
    all_findings = disambiguate(findings)
    kept, suppressed, used_bare, used_specific = _apply_suppressions(
        all_findings, file_suppressions, strict_suppressions
    )
    problems = _suppression_problems(
        file_suppressions,
        {rule.rule_id for rule in rules},
        strict_suppressions,
        used_bare,
        used_specific,
    )

    return ScanResult(
        findings=tuple(kept),
        errors=tuple(errors),
        skipped=discovery.skipped,
        files_scanned=files_scanned,
        cache_hits=(cache.hits - hits_before) if cache is not None else 0,
        cache_misses=(cache.misses - misses_before) if cache is not None else 0,
        suppressed=tuple(suppressed),
        suppression_problems=tuple(problems),
    )


def _apply_suppressions(
    all_findings: Sequence[Finding],
    file_suppressions: dict[str, tuple[list[Suppression], list[tuple[int, str]]]],
    strict: bool,
) -> tuple[list[Finding], list[Finding], set[tuple[str, int]], set[tuple[str, int, str]]]:
    """Split findings into kept and suppressed ones, preserving their order.

    Returns:
        ``(kept, suppressed, used_bare, used_specific)`` where ``used_bare`` holds
        ``(path, line)`` of bare suppressions that matched and ``used_specific``
        holds ``(path, line, rule_id)`` of specific suppressions that matched.
    """
    by_line: dict[tuple[str, int], list[Suppression]] = {}
    for relpath, (suppressions, _) in file_suppressions.items():
        for item in suppressions:
            by_line.setdefault((relpath, item.line), []).append(item)

    kept: list[Finding] = []
    suppressed: list[Finding] = []
    used_bare: set[tuple[str, int]] = set()
    used_specific: set[tuple[str, int, str]] = set()
    for finding in all_findings:
        loc = finding.location
        if loc is None or loc.line is None:
            kept.append(finding)
            continue
        hit = False
        for item in by_line.get((loc.path, loc.line), []):
            if item.rule_ids is None:
                if not strict:
                    hit = True
                    used_bare.add((loc.path, loc.line))
            elif finding.rule_id in item.rule_ids:
                hit = True
                used_specific.add((loc.path, loc.line, finding.rule_id))
        (suppressed if hit else kept).append(finding)
    return kept, suppressed, used_bare, used_specific


def _suppression_problems(
    file_suppressions: dict[str, tuple[list[Suppression], list[tuple[int, str]]]],
    active_ids: set[str],
    strict: bool,
    used_bare: set[tuple[str, int]],
    used_specific: set[tuple[str, int, str]],
) -> list[SuppressionProblem]:
    """Return malformed, bare and stale suppression problems, sorted."""
    problems: list[SuppressionProblem] = []
    for relpath, (suppressions, malformed) in file_suppressions.items():
        for line, message in malformed:
            problems.append(SuppressionProblem(relpath, line, "malformed", message, None))
        for item in suppressions:
            problems.extend(
                _item_problems(
                    relpath,
                    item,
                    active_ids=active_ids,
                    strict=strict,
                    used_bare=used_bare,
                    used_specific=used_specific,
                )
            )
    problems.sort(key=lambda p: (p.relpath, p.line, p.kind, p.message))
    return problems


def _item_problems(
    relpath: str,
    item: Suppression,
    *,
    active_ids: set[str],
    strict: bool,
    used_bare: set[tuple[str, int]],
    used_specific: set[tuple[str, int, str]],
) -> list[SuppressionProblem]:
    """Return the bare or stale problems for one well-formed suppression."""
    if item.rule_ids is None:
        if strict:
            message = (
                f"suppression on line {item.line} does not name a rule; "
                "strict mode requires ignore[RULE-ID]"
            )
            return [SuppressionProblem(relpath, item.line, "bare", message, None)]
        if (relpath, item.line) in used_bare:
            return []
        message = f"suppression on line {item.line} matches no finding"
        return [SuppressionProblem(relpath, item.line, "stale", message, None)]
    out: list[SuppressionProblem] = []
    for rule_id in item.rule_ids:
        if rule_id in active_ids and (relpath, item.line, rule_id) not in used_specific:
            message = (
                f"{rule_id} is suppressed on line {item.line} but no {rule_id} "
                "finding is reported there"
            )
            out.append(SuppressionProblem(relpath, item.line, "stale", message, rule_id))
    return out
