"""Redaction helpers that keep secrets and home-directory paths out of reports.

Security requirement: secret values must never reach a report.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

REDACTED: Final[str] = "[REDACTED]"
_MIN_SECRET_LENGTH: Final = 4


class _HomeDefault:
    """Sentinel type: an omitted ``home`` argument means ``Path.home()``."""

    def __repr__(self) -> str:
        """Return a readable name for the sentinel."""
        return "<default home>"


_HOME_DEFAULT: Final[_HomeDefault] = _HomeDefault()

_PRIVATE_KEY_RE = re.compile(
    # A missing END marker (truncated output) masks everything to the end of the text.
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_URL_CREDENTIALS_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]+:)[^\s/@]+(@)")
_BEARER_RE = re.compile(r"(Bearer[ \t]+)[^\s\"',]+", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(
    r"(?P<head>[\"']?[A-Za-z0-9_.-]*"
    r"(?:password|passwd|pwd|secret|token|api_key|apikey|api-key|access_key"
    r"|private_key|credentials|credential)"
    r"[\"']?[ \t]*[=:][ \t]*)"
    r"(?P<quote>[\"']?)"
    r"[^\s\"',]+",
    re.IGNORECASE,
)
_TOKEN_RES: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern)
    for pattern in (
        r"gh[pousr]_[A-Za-z0-9]{36,}",
        r"github_pat_[A-Za-z0-9_]{22,}",
        r"AKIA[0-9A-Z]{16}",
        r"xox[abprs]-[A-Za-z0-9-]{10,}",
        r"sk-[A-Za-z0-9_-]{20,}",
        r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    )
)


def _resolve_home(home: str | os.PathLike[str] | _HomeDefault | None) -> str | None:
    """Return the home directory string to hide, or None when disabled."""
    if isinstance(home, _HomeDefault):
        return str(Path.home())
    if home is None:
        return None
    return os.fspath(home)


def _home_pattern(home: str) -> re.Pattern[str] | None:
    """Build a case-insensitive pattern matching ``home`` with either separator."""
    trimmed = home.rstrip("\\/")
    if not trimmed:
        return None
    segments = [re.escape(segment) for segment in re.split(r"[\\/]+", trimmed) if segment]
    lead = r"[\\/]" if home[:1] in ("\\", "/") else ""
    body = lead + r"[\\/]".join(segments)
    # Not followed by [\w.-] so that e.g. "alicesmith" is not treated as "alice";
    # not preceded by a path character so "/srv/home/bob" is not treated as "/home/bob".
    return re.compile(r"(?<![\w.\\/-])" + body + r"(?![\w.-])", re.IGNORECASE)


def redact_text(
    text: str,
    *,
    home: str | os.PathLike[str] | _HomeDefault | None = _HOME_DEFAULT,
    extra_secrets: Iterable[str] = (),
    max_length: int = 2000,
) -> str:
    """Remove secrets and the home directory from ``text`` and truncate it.

    Args:
        text: Text to sanitise.
        home: Home directory to replace with ``~``. Omitted means
            ``Path.home()``; ``None`` disables home replacement.
        extra_secrets: Literal secret values to mask. Entries shorter than
            4 characters are ignored.
        max_length: Maximum output length before truncation.

    Raises:
        ValueError: If ``max_length`` is less than 1.
    """
    if max_length < 1:
        raise ValueError(f"max_length must be >= 1, got {max_length}")

    out = _PRIVATE_KEY_RE.sub(REDACTED, text)
    out = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(2)}", out)
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    out = _ASSIGNMENT_RE.sub(lambda m: f"{m.group('head')}{m.group('quote')}{REDACTED}", out)
    for pattern in _TOKEN_RES:
        out = pattern.sub(REDACTED, out)

    secrets = sorted(
        {s for s in extra_secrets if len(s) >= _MIN_SECRET_LENGTH}, key=lambda s: (-len(s), s)
    )
    for secret in secrets:
        out = out.replace(secret, REDACTED)

    home_value = _resolve_home(home)
    if home_value is not None:
        home_re = _home_pattern(home_value)
        if home_re is not None:
            out = home_re.sub("~", out)

    if len(out) > max_length:
        excess = len(out) - max_length
        out = out[:max_length] + f"...[truncated {excess} chars]"
    return out
