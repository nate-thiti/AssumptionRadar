"""Redaction helpers that keep secrets and home-directory paths out of reports.

Security requirement: secret values must never reach a report.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

REDACTED: Final[str] = "[REDACTED]"
_MIN_SECRET_LENGTH: Final = 4
_MIN_ENV_SECRET_LENGTH: Final = 8
_SECRET_NAME_WORDS: Final[frozenset[str]] = frozenset(
    {"TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "CREDENTIALS", "APIKEY"}
)


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
# Patterns below must stay linear in the input length: child output can be large.
# The scheme is capped at 32 characters so a long run of letters is not rescanned
# from every position (quadratic backtracking).
_URL_CREDENTIALS_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s:/@]+:)[^\s/@]+(@)")
_BEARER_RE = re.compile(r"(Bearer[ \t]+)[^\s\"',]+", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(
    # The key must end with a secret word; any name before it is left out of the
    # match on purpose (it is never rewritten, and matching it was quadratic).
    r"(?P<head>"
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


def mask_values(text: str, values: Iterable[str]) -> str:
    """Replace each literal value in ``text`` with ``REDACTED``.

    Longer values are replaced first so that a value containing another value
    is masked whole. Values shorter than 4 characters are ignored.

    Args:
        text: Text to mask.
        values: Literal secret values to replace.

    Returns:
        The masked text.
    """
    secrets = sorted(
        {v for v in values if len(v) >= _MIN_SECRET_LENGTH}, key=lambda v: (-len(v), v)
    )
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def secret_env_values(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Return the values of environment variables whose names look secret.

    A name is secret-looking when one of its ``_``-separated words is a secret
    word (such as ``TOKEN`` or ``PASSWORD``), or when its last word is ``KEY``.
    Matching is case-insensitive and word-based, so ``TOKENIZERS`` is not secret.

    Values shorter than 8 characters are skipped. Short values such as ``true``
    or ``1234567`` would mask ordinary words in report output.

    Args:
        environ: Mapping to inspect. None means ``os.environ``.

    Returns:
        The qualifying values as a frozenset.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    values: set[str] = set()
    for name, value in source.items():
        words = name.upper().split("_")
        is_secret = words[-1] == "KEY" or any(word in _SECRET_NAME_WORDS for word in words)
        if is_secret and len(value) >= _MIN_ENV_SECRET_LENGTH:
            values.add(value)
    return frozenset(values)


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

    out = mask_values(out, extra_secrets)

    home_value = _resolve_home(home)
    if home_value is not None:
        home_re = _home_pattern(home_value)
        if home_re is not None:
            out = home_re.sub("~", out)

    if len(out) > max_length:
        excess = len(out) - max_length
        out = out[:max_length] + f"...[truncated {excess} chars]"
    return out
