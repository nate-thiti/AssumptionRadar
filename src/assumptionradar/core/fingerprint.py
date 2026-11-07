"""Stable finding fingerprints used for baselines and deduplication (ADR-0004)."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Iterable

from assumptionradar.core.models import Finding

_SUFFIX_RE = re.compile(r":[0-9]+$")


def normalize_key(key: str) -> str:
    """Collapse all whitespace runs in ``key`` to single spaces and strip the ends."""
    return " ".join(key.split())


def compute_fingerprint(rule_id: str, path: str | None, symbol: str | None, key: str) -> str:
    """Return a 16-hex-char fingerprint derived from the rule, location and key.

    Backslashes in ``path`` are normalised to ``/``, and ``None`` is equivalent
    to the empty string for ``path`` and ``symbol``.
    """
    payload = "\x1f".join(
        [
            rule_id,
            (path or "").replace("\\", "/"),
            symbol or "",
            normalize_key(key),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def disambiguate(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Make fingerprints unique within one run.

    Existing ``:<n>`` suffixes are removed first. Findings are then sorted by
    ``sort_key``; the first occurrence of each fingerprint keeps it unchanged and
    later occurrences get ``:2``, ``:3``, ... in that order.
    """
    base = [
        dataclasses.replace(finding, fingerprint=_SUFFIX_RE.sub("", finding.fingerprint))
        for finding in findings
    ]
    ordered = sorted(base, key=lambda item: item.sort_key)
    seen: dict[str, int] = {}
    out: list[Finding] = []
    for finding in ordered:
        count = seen.get(finding.fingerprint, 0) + 1
        seen[finding.fingerprint] = count
        if count == 1:
            out.append(finding)
        else:
            out.append(dataclasses.replace(finding, fingerprint=f"{finding.fingerprint}:{count}"))
    return tuple(out)
