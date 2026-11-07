"""Terminal renderer for v1 assumption reports.

The renderer turns a v1 report dictionary (the document written by
``--assumption-report``) into a list of styled :class:`Line` objects. The pytest
plugin summary and ``report show`` share it so both print the same wording.
Rendering never mutates its input.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from assumptionradar.analysis.runtime import EventKind
from assumptionradar.core.catalog import SCENARIO_CATALOG

ANSI_STYLES: Final[Mapping[str, str]] = {
    "red": "\x1b[31m",
    "yellow": "\x1b[33m",
    "green": "\x1b[32m",
    "bold": "\x1b[1m",
}
"""ANSI escape sequence for each style name accepted by :class:`Line`."""

ANSI_RESET: Final = "\x1b[0m"
"""ANSI escape sequence that clears all styles."""

FIX_HINTS: Final[Mapping[str, str]] = MappingProxyType(
    {info.family: info.fix for info in SCENARIO_CATALOG}
)
"""Suggested fix for each scenario family, keyed by the part before the first colon."""

_RANK: Final[tuple[str, ...]] = (
    "regression",
    "error",
    "inconclusive",
    "unsupported",
    "no_difference",
    "not_verified",
)
_STATUS_STYLE: Final[Mapping[str, str | None]] = {
    "regression": "red",
    "error": "red",
    "inconclusive": "yellow",
    "unsupported": "yellow",
    "no_difference": "green",
    "not_verified": None,
}


@dataclass(frozen=True, slots=True)
class Line:
    """One line of terminal output.

    Attributes:
        text: The plain text of the line, without colour codes.
        style: Optional style name, a key of :data:`ANSI_STYLES`.

    Raises:
        ValueError: If ``style`` is not None and not a key of :data:`ANSI_STYLES`.
    """

    text: str
    style: str | None = None

    def __post_init__(self) -> None:
        """Reject unknown style names."""
        if self.style is not None and self.style not in ANSI_STYLES:
            raise ValueError(f"unknown style {self.style!r}")


def use_color(stream: object, environ: Mapping[str, str] | None = None) -> bool:
    """Decide whether output written to ``stream`` should carry colour codes.

    ``NO_COLOR`` (non-empty) disables colour and wins over everything. Otherwise
    a non-empty ``FORCE_COLOR`` enables it. Otherwise the stream's ``isatty()``
    result is used.

    Args:
        stream: The output stream to inspect.
        environ: Environment mapping to read; defaults to :data:`os.environ`.

    Returns:
        True when colour codes should be written.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    if env.get("NO_COLOR"):
        return False
    if env.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False


def to_text(lines: Iterable[Line], *, color: bool) -> str:
    """Join lines into one string, optionally wrapping styled lines in ANSI codes.

    Args:
        lines: The lines to join.
        color: Whether to wrap styled lines in their ANSI sequences.

    Returns:
        The lines joined by newlines with a trailing newline, or an empty string
        when there are no lines.
    """
    rendered: list[str] = []
    for line in lines:
        if color and line.style is not None:
            rendered.append(f"{ANSI_STYLES[line.style]}{line.text}{ANSI_RESET}")
        else:
            rendered.append(line.text)
    if not rendered:
        return ""
    return "\n".join(rendered) + "\n"


def fix_hint(scenario: str) -> str | None:
    """Return the suggested fix for a scenario, if its family is known.

    Args:
        scenario: A scenario name such as ``cwd`` or ``tz:JST-9``.

    Returns:
        The hint text, or None when the scenario family has no hint.
    """
    family = scenario.split(":", 1)[0]
    return FIX_HINTS.get(family)


def _plural(count: int, word: str) -> str:
    """Format a count with its noun, adding ``s`` unless the count is 1."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _rank(status: str) -> int:
    """Return the severity rank of a status; unknown statuses sort last."""
    try:
        return _RANK.index(status)
    except ValueError:
        return len(_RANK)


def _verification_section(
    verifications: list[Mapping[str, Any]],
    skipped: list[Mapping[str, Any]],
    max_skipped: int,
) -> list[Line]:
    """Build the verification part of the report."""
    lines: list[Line] = []
    if not verifications:
        lines.append(Line("verification: no tests verified", "bold"))
    else:
        counts: dict[str, int] = {}
        for item in verifications:
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        parts = [f"{counts[status]} {status}" for status in _RANK if counts.get(status)]
        header = f"verification: {', '.join(parts)} ({_plural(len(verifications), 'test')})"
        lines.append(Line(header, "bold"))
        ordered = sorted(verifications, key=lambda item: _rank(str(item["status"])))
        for item in ordered:
            status = str(item["status"])
            lines.append(
                Line(f"  {status.upper():<14}{item['test_id']}", _STATUS_STYLE.get(status))
            )
            for scenario in item["scenarios"]:
                name = str(scenario["scenario"])
                scenario_status = str(scenario["status"])
                reason = str(scenario["reason"])
                if scenario_status == "no_difference":
                    continue
                if scenario_status == "regression":
                    lines.append(Line(f"      {name}: regression (reproduced) - {reason}"))
                    hint = fix_hint(name)
                    if hint is not None:
                        lines.append(Line(f"        fix: {hint}"))
                else:
                    lines.append(Line(f"      {name}: {scenario_status} - {reason}"))
    if skipped:
        lines.append(Line(f"  skipped {_plural(len(skipped), 'test')}:"))
        for item in skipped[:max_skipped]:
            lines.append(Line(f"    {item['test_id']} - {item['reason']}"))
        extra = len(skipped) - max_skipped
        if extra > 0:
            lines.append(Line(f"    ... {extra} more"))
    return lines


def _is_attention(event: Mapping[str, Any]) -> bool:
    """Return True for events that deserve the attention marker."""
    attrs: Mapping[str, Any] = event["attributes"]
    return bool(
        attrs.get("relative") == "true"
        or attrs.get("path_lookup") == "true"
        or (event["kind"] == "env_read" and attrs.get("present") == "false")
    )


def _event_line(event: Mapping[str, Any], attention: bool) -> Line:
    """Format one observation event as a line."""
    attrs: Mapping[str, Any] = event["attributes"]
    attr_text = ", ".join(f"{key}={attrs[key]}" for key in sorted(attrs))
    bracket = f" [{attr_text}]" if attr_text else ""
    location: Mapping[str, Any] | None = event["location"]
    if location is None:
        where = "unknown location"
    elif location["line"] is None:
        where = str(location["path"])
    else:
        where = f"{location['path']}:{location['line']}"
    test_id = event["test_id"] or "collection"
    mark = "!" if attention else " "
    text = f"  {mark} {event['kind']} {event['target']}{bracket} at {where} ({test_id})"
    return Line(text, "yellow" if attention else None)


def _observation_section(observation: Mapping[str, Any], max_events: int) -> list[Line]:
    """Build the observation part of the report."""
    events: list[Mapping[str, Any]] = list(observation["events"])
    errors: list[str] = [str(error) for error in observation["errors"]]
    counts: dict[str, int] = {}
    for event in events:
        kind = str(event["kind"])
        counts[kind] = counts.get(kind, 0) + 1
    ordered_kinds: list[str] = [kind.value for kind in EventKind if kind.value in counts]
    ordered_kinds += [kind for kind in counts if kind not in ordered_kinds]
    count_text = ", ".join(f"{kind}: {counts[kind]}" for kind in ordered_kinds)
    header = f"observation: {_plural(len(events), 'event')}"
    if events:
        header += f" ({count_text})"
    header += f"; {observation['unattributed']} unattributed, {observation['dropped']} dropped"

    lines: list[Line] = [Line(header, "bold")]
    if errors:
        lines.append(
            Line(
                f"  {_plural(len(errors), 'internal observer error')}; first: {errors[0]}",
                "red",
            )
        )
    attention = [event for event in events if _is_attention(event)]
    others = [event for event in events if not _is_attention(event)]
    shown = [(event, True) for event in attention] + [(event, False) for event in others]
    for event, flagged in shown[:max_events]:
        lines.append(_event_line(event, flagged))
    extra = len(shown) - max_events
    if extra > 0:
        noun = "event" if extra == 1 else "events"
        lines.append(Line(f"  ... {extra} more {noun}; see the JSON report for all"))
    return lines


def render_report(
    report: Mapping[str, Any],
    *,
    max_events: int = 20,
    max_skipped: int = 10,
) -> list[Line]:
    """Render a v1 report dictionary as styled terminal lines.

    Args:
        report: A v1 report document. It is read but never modified.
        max_events: Maximum number of observation events to list.
        max_skipped: Maximum number of skipped tests to list.

    Returns:
        The lines to print, verification section first, then observation.
    """
    verifications: list[Mapping[str, Any]] = list(report.get("verifications") or [])
    skipped: list[Mapping[str, Any]] = list(report.get("verification_skipped") or [])
    observation: Mapping[str, Any] | None = report.get("observation")

    sections: list[list[Line]] = []
    if verifications or skipped:
        sections.append(_verification_section(verifications, skipped, max_skipped))
    if observation is not None:
        sections.append(_observation_section(observation, max_events))
    if not sections:
        return [Line("nothing observed or verified")]

    lines: list[Line] = []
    for index, section in enumerate(sections):
        if index:
            lines.append(Line(""))
        lines.extend(section)
    return lines
