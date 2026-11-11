"""Core data model for AssumptionRadar findings (ADR-0004).

Every public type here is immutable and serialises to plain JSON-compatible
values through ``to_dict``.
"""

from __future__ import annotations

import dataclasses
import os
import re
import types
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}(?::[0-9]+)?$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class _ParsableStrEnum(StrEnum):
    """Base for string enums that can be parsed case- and space-insensitively."""

    @classmethod
    def parse(cls, text: str) -> Self:
        """Return the member whose value matches ``text`` after strip and lower.

        Raises:
            ValueError: If no member matches. The message lists valid values.
        """
        normalized = text.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        valid = ", ".join(member.value for member in cls)
        raise ValueError(f"invalid {cls.__name__} {text!r}; expected one of: {valid}")


class Severity(_ParsableStrEnum):
    """How serious a finding is."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        """Ordinal rank in declaration order (info=0, warning=1, error=2)."""
        return list(Severity).index(self)

    def at_least(self, other: Severity) -> bool:
        """Return True when this severity is at or above ``other``."""
        return self.rank >= other.rank


class Confidence(_ParsableStrEnum):
    """How sure the analyzer is that a finding is real."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        """Ordinal rank in declaration order (low=0, medium=1, high=2)."""
        return list(Confidence).index(self)


class EvidenceKind(_ParsableStrEnum):
    """How a piece of evidence was obtained."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    REPRODUCED = "reproduced"


class VerificationStatus(_ParsableStrEnum):
    """Outcome of verifying a finding by re-running code."""

    NOT_VERIFIED = "not_verified"
    REGRESSION = "regression"
    NO_DIFFERENCE = "no_difference"
    INCONCLUSIVE = "inconclusive"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class Category(_ParsableStrEnum):
    """Built-in finding categories."""

    CWD = "cwd"
    ENVIRONMENT = "environment"
    FILESYSTEM = "filesystem"
    TIMEZONE = "timezone"
    LOCALE = "locale"
    ENCODING = "encoding"
    NETWORK = "network"
    EXECUTABLES = "executables"
    PLATFORM = "platform"
    TIME = "time"
    IMPORTS = "imports"
    TEST_ORDER = "test_order"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A repository-relative, POSIX-style location in source code.

    Attributes:
        path: Relative POSIX path (no drive, no leading slash, no ``..``).
        line: 1-based line number, if known.
        column: 1-based column number, requires ``line``.
        symbol: Enclosing function/class name, if known.
    """

    path: str
    line: int | None = None
    column: int | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        """Validate the location fields."""
        if not self.path:
            raise ValueError("path must not be empty")
        if self.path.startswith("/"):
            raise ValueError(f"path must be relative, got {self.path!r}")
        if _WINDOWS_DRIVE_RE.match(self.path):
            raise ValueError(f"path must not contain a drive letter, got {self.path!r}")
        if "\\" in self.path:
            raise ValueError(f"path must use '/' separators, got {self.path!r}")
        if any(segment == ".." for segment in self.path.split("/")):
            raise ValueError(f"path must not contain '..', got {self.path!r}")
        if self.line is not None and self.line < 1:
            raise ValueError(f"line must be >= 1, got {self.line}")
        if self.column is not None and self.column < 1:
            raise ValueError(f"column must be >= 1, got {self.column}")
        if self.column is not None and self.line is None:
            raise ValueError("column requires line")

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        root: str | os.PathLike[str],
        line: int | None = None,
        column: int | None = None,
        symbol: str | None = None,
    ) -> Self:
        """Build a location from a filesystem path relative to ``root``.

        Raises:
            ValueError: If ``path`` resolves to a location outside ``root``.
        """
        resolved_root = Path(root).resolve()
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"path {str(path)!r} is outside root {str(root)!r}") from exc
        return cls(relative.as_posix(), line=line, column=column, symbol=symbol)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a location from the output of ``to_dict``."""
        return cls(
            path=str(data["path"]),
            line=data.get("line"),
            column=data.get("column"),
            symbol=data.get("symbol"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    """A single observation supporting a finding."""

    kind: EvidenceKind
    method: str
    detail: str
    location: SourceLocation | None = None

    def __post_init__(self) -> None:
        """Validate that the method is named."""
        if not self.method.strip():
            raise ValueError("evidence method must not be blank")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild evidence from the output of ``to_dict``."""
        location = data.get("location")
        return cls(
            kind=EvidenceKind.parse(str(data["kind"])),
            method=str(data["method"]),
            detail=str(data["detail"]),
            location=SourceLocation.from_dict(location) if location is not None else None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict."""
        return {
            "kind": str(self.kind),
            "method": self.method,
            "detail": self.detail,
            "location": self.location.to_dict() if self.location is not None else None,
        }


@dataclass(frozen=True, slots=True)
class Finding:
    """A single assumption detected in code, with its verification state."""

    rule_id: str
    title: str
    message: str
    category: str
    severity: Severity
    confidence: Confidence
    fingerprint: str
    location: SourceLocation | None = None
    evidence: tuple[Evidence, ...] = ()
    verification: VerificationStatus = VerificationStatus.NOT_VERIFIED
    remediation: str = ""
    failure_scenario: str = ""
    docs_url: str | None = None
    test_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        """Validate identifiers and coerce containers to immutable types."""
        if _RULE_ID_RE.fullmatch(self.rule_id) is None:
            raise ValueError(f"invalid rule_id {self.rule_id!r}; expected e.g. 'AR-CWD-001'")
        if _FINGERPRINT_RE.fullmatch(self.fingerprint) is None:
            raise ValueError(
                f"invalid fingerprint {self.fingerprint!r}; expected 16 lowercase hex "
                "characters with an optional ':<n>' suffix"
            )
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if not self.message.strip():
            raise ValueError("message must not be blank")
        for key, value in dict(self.metadata).items():
            value_obj: object = value
            if not isinstance(value_obj, str):
                raise TypeError(
                    f"metadata value for {key!r} must be str, got {type(value_obj).__name__}"
                )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(
            self,
            "metadata",
            types.MappingProxyType(dict(sorted(dict(self.metadata).items()))),
        )

    @property
    def evidence_kinds(self) -> frozenset[EvidenceKind]:
        """The set of evidence kinds attached to this finding."""
        return frozenset(item.kind for item in self.evidence)

    @property
    def sort_key(self) -> tuple[str, int, int, str, str]:
        """Deterministic ordering key: location, then rule id, then fingerprint."""
        loc = self.location
        if loc is None:
            return ("", 0, 0, self.rule_id, self.fingerprint)
        return (loc.path, loc.line or 0, loc.column or 0, self.rule_id, self.fingerprint)

    def with_verification(
        self,
        status: VerificationStatus,
        evidence: Evidence | None = None,
    ) -> Finding:
        """Return a copy with a new verification status and optional extra evidence."""
        new_evidence = self.evidence if evidence is None else (*self.evidence, evidence)
        return dataclasses.replace(self, verification=status, evidence=new_evidence)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a finding from the output of ``to_dict``."""
        location = data.get("location")
        return cls(
            rule_id=str(data["rule_id"]),
            title=str(data["title"]),
            message=str(data["message"]),
            category=str(data["category"]),
            severity=Severity.parse(str(data["severity"])),
            confidence=Confidence.parse(str(data["confidence"])),
            fingerprint=str(data["fingerprint"]),
            location=SourceLocation.from_dict(location) if location is not None else None,
            evidence=tuple(Evidence.from_dict(item) for item in data.get("evidence") or ()),
            verification=VerificationStatus.parse(str(data.get("verification", "not_verified"))),
            remediation=str(data.get("remediation", "")),
            failure_scenario=str(data.get("failure_scenario", "")),
            docs_url=data.get("docs_url"),
            test_id=data.get("test_id"),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed key order."""
        return {
            "rule_id": self.rule_id,
            "fingerprint": self.fingerprint,
            "title": self.title,
            "message": self.message,
            "category": str(self.category),
            "severity": str(self.severity),
            "confidence": str(self.confidence),
            "verification": str(self.verification),
            "location": self.location.to_dict() if self.location is not None else None,
            "test_id": self.test_id,
            "evidence": [item.to_dict() for item in self.evidence],
            "failure_scenario": self.failure_scenario,
            "remediation": self.remediation,
            "docs_url": self.docs_url,
            "metadata": dict(self.metadata),
        }
