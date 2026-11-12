"""Inline suppression comments for the static scanner (P2-7).

A suppression is a comment of the form
``# assumptionradar: ignore[RULE-ID, ...] -- reason``. Comments are found with
the tokenizer, so text inside string literals is never mistaken for one.
"""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass
from typing import Any

MALFORMED = (
    "malformed suppression comment; expected '# assumptionradar: ignore[RULE-ID, ...] -- reason'"
)

_PREFIX_RE = re.compile(r"^#\s*assumptionradar\s*:(?P<body>.*)$", re.IGNORECASE)
_BODY_RE = re.compile(r"ignore(?:\[(?P<ids>[^\]]*)\])?(?:\s*--\s*(?P<reason>.*))?")
_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


@dataclass(frozen=True, slots=True)
class Suppression:
    """A well-formed suppression comment.

    Attributes:
        line: 1-based line of the comment.
        column: 1-based column of the comment's ``#``.
        rule_ids: Rule ids named in the comment, or ``None`` for a bare
            suppression that applies to all rules.
        reason: Text after ``--``, stripped; ``None`` when empty.
    """

    line: int
    column: int
    rule_ids: tuple[str, ...] | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class SuppressionProblem:
    """A problem with a suppression comment or with its use.

    Attributes:
        relpath: POSIX path relative to the project root.
        line: 1-based line of the comment.
        kind: One of ``"malformed"``, ``"bare"`` or ``"stale"``.
        message: Human-readable description.
        rule_id: The rule id concerned, if any.
    """

    relpath: str
    line: int
    kind: str
    message: str
    rule_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "relpath": self.relpath,
            "line": self.line,
            "kind": self.kind,
            "message": self.message,
            "rule_id": self.rule_id,
        }


def parse_suppressions(source: str) -> tuple[list[Suppression], list[tuple[int, str]]]:
    """Return the suppression comments in ``source`` and the malformed ones.

    Args:
        source: Python source text.

    Returns:
        ``(suppressions, malformed)`` where ``malformed`` holds ``(line, message)``
        pairs. Source that cannot be tokenized yields two empty lists.
    """
    suppressions: list[Suppression] = []
    malformed: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type != tokenize.COMMENT:
                continue
            prefix = _PREFIX_RE.match(tok.string)
            if prefix is None:
                continue
            line, col = tok.start
            body = prefix.group("body").strip()
            match = _BODY_RE.fullmatch(body)
            if match is None:
                malformed.append((line, MALFORMED))
                continue
            ids_text = match.group("ids")
            rule_ids: tuple[str, ...] | None = None
            if ids_text is not None:
                if not ids_text.strip():
                    malformed.append((line, MALFORMED))
                    continue
                ids = tuple(part.strip() for part in ids_text.split(","))
                bad = next((rid for rid in ids if _RULE_ID_RE.fullmatch(rid) is None), None)
                if bad is not None:
                    malformed.append((line, f"invalid rule id {bad!r} in suppression comment"))
                    continue
                rule_ids = ids
            reason = (match.group("reason") or "").strip() or None
            suppressions.append(Suppression(line, col + 1, rule_ids, reason))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return [], []
    return suppressions, malformed
