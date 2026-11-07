"""Controlled environment scenarios for re-running a test (ADR-0003, Spike 0001).

Each factory returns a :class:`Scenario` describing one condition under which the
selected test is re-run. Expected probe values are reported by the child process,
so a condition that did not take effect is reported as unsupported, not as a
regression.
"""

from __future__ import annotations

import codecs
import locale
import os
import sys
import time
from collections.abc import Callable, Mapping

from assumptionradar.analysis.verification import Scenario

# 2023-11-14T22:13:20Z, outside northern-hemisphere DST, so EST5EDT -> -5h.
REFERENCE_TIMESTAMP = 1_700_000_000

# zone key -> (POSIX TZ string, UTC offset in seconds). POSIX strings only:
# IANA names are silently ignored by the Windows CRT (Spike 0001).
TZ_ZONES: dict[str, tuple[str, int]] = {
    "+9": ("JST-9", 32400),
    "-5": ("EST5EDT", -18000),
    "utc": ("UTC0", 0),
}

_TZ_LABELS: dict[str, str] = {"+9": "UTC+9", "-5": "UTC-5", "utc": "UTC"}

# locale key -> (POSIX locale name, Windows locale name)
LOCALES: dict[str, tuple[str, str]] = {
    "de_DE": ("de_DE.UTF-8", "de-DE"),
    "fr_FR": ("fr_FR.UTF-8", "fr-FR"),
    "tr_TR": ("tr_TR.UTF-8", "tr-TR"),
}

_ZONE_HINT = ", ".join(TZ_ZONES)
_LOCALE_HINT = ", ".join(LOCALES)
_SCENARIO_HINT = "default, cwd, tmpdir, encoding, tz, tz:<zone>, locale:<name>, env-unset:<NAME>"


def cwd_scenario() -> Scenario:
    """Return the scenario that runs the test from an empty temporary directory."""
    return Scenario(
        name="cwd",
        description="Run from an empty temporary working directory",
        isolated_cwd=True,
        expect={"cwd": "{isolated_cwd}"},
    )


def tmpdir_scenario() -> Scenario:
    """Return the scenario that uses a temporary directory with spaces and non-ASCII characters."""
    return Scenario(
        name="tmpdir",
        description="Temporary directory path contains spaces and non-ASCII characters",
        isolated_tmp=True,
        expect={"tempdir": "{isolated_tmp}"},
    )


def tz_scenario(zone: str) -> Scenario:
    """Return the scenario that sets the local timezone to ``zone``.

    Raises:
        ValueError: If ``zone`` is not one of the keys of ``TZ_ZONES``.
    """
    if zone not in TZ_ZONES:
        raise ValueError(f"unknown timezone {zone!r}; expected one of: {_ZONE_HINT}")
    posix, offset = TZ_ZONES[zone]
    return Scenario(
        name=f"tz:{zone}",
        description=f"Local timezone {_TZ_LABELS[zone]} (TZ={posix})",
        env_set={"TZ": posix},
        expect={"utc_offset_seconds": str(offset)},
    )


def default_tz_scenarios(host_offset: int | None = None) -> tuple[Scenario, ...]:
    """Return up to two timezone scenarios whose offset differs from the host offset.

    Args:
        host_offset: Host UTC offset in seconds. None means the offset at REFERENCE_TIMESTAMP.
    """
    if host_offset is None:
        host_offset = time.localtime(REFERENCE_TIMESTAMP).tm_gmtoff
    chosen = [zone for zone, (_, offset) in TZ_ZONES.items() if offset != host_offset]
    return tuple(tz_scenario(zone) for zone in chosen[:2])


def encoding_scenario(host_is_utf8: bool | None = None) -> Scenario:
    """Return the scenario that switches the preferred encoding away from the host's.

    Args:
        host_is_utf8: Whether the host preferred encoding is UTF-8. None means detect it.
    """
    if host_is_utf8 is None:
        host_is_utf8 = codecs.lookup(locale.getpreferredencoding(False)).name == "utf-8"
    if not host_is_utf8:
        return Scenario(
            name="encoding:utf-8",
            description="Python UTF-8 mode is enabled (PYTHONUTF8=1)",
            env_set={"PYTHONUTF8": "1"},
            expect={"preferred_encoding_utf8": "true"},
        )
    return Scenario(
        name="encoding:legacy",
        description="Legacy non-UTF-8 encoding (PYTHONUTF8=0, PYTHONCOERCECLOCALE=0, LC_ALL=C)",
        env_set={"PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"},
        expect={"preferred_encoding_utf8": "false"},
    )


def locale_scenario(name: str, *, platform: str = sys.platform) -> Scenario:
    """Return the scenario that requests locale ``name`` for the child process.

    Raises:
        ValueError: If ``name`` is not one of the keys of ``LOCALES``.
    """
    if name not in LOCALES:
        raise ValueError(f"unknown locale {name!r}; expected one of: {_LOCALE_HINT}")
    posix, windows = LOCALES[name]
    child_locale = windows if platform == "win32" else posix
    return Scenario(
        name=f"locale:{name}",
        description=f"Locale {child_locale} requested for the child process",
        child_locale=child_locale,
        expect={"locale_applied": "true"},
    )


def env_unset_scenario(name: str, environ: Mapping[str, str] | None = None) -> Scenario:
    """Return the scenario that removes environment variable ``name`` for the child.

    The variable's value is never stored in the scenario.

    Args:
        name: Environment variable name to remove.
        environ: Environment to check presence against. None means ``os.environ``.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    reason: str | None = None
    if name not in source:
        reason = f"{name} is not set in the current environment; removing it would change nothing"
    return Scenario(
        name=f"env-unset:{name}",
        description=f"Environment variable {name} is removed",
        env_unset=(name,),
        expect={f"env:{name}": "absent"},
        unsupported_reason=reason,
    )


def default_scenarios(
    host_offset: int | None = None, host_is_utf8: bool | None = None
) -> tuple[Scenario, ...]:
    """Return the default scenario set: cwd, two timezones, encoding and tmpdir."""
    return (
        cwd_scenario(),
        *default_tz_scenarios(host_offset),
        encoding_scenario(host_is_utf8),
        tmpdir_scenario(),
    )


def _expand_item(
    item: str,
    *,
    environ: Mapping[str, str] | None,
    host_offset: int | None,
    host_is_utf8: bool | None,
) -> tuple[Scenario, ...]:
    """Expand one spec item into its scenarios.

    Raises:
        ValueError: If the item is not a valid scenario specifier.
    """
    simple: dict[str, Callable[[], tuple[Scenario, ...]]] = {
        "default": lambda: default_scenarios(host_offset=host_offset, host_is_utf8=host_is_utf8),
        "cwd": lambda: (cwd_scenario(),),
        "tmpdir": lambda: (tmpdir_scenario(),),
        "encoding": lambda: (encoding_scenario(host_is_utf8),),
        "tz": lambda: default_tz_scenarios(host_offset),
    }
    if item in simple:
        return simple[item]()
    kind, sep, arg = item.partition(":")
    if sep and kind == "tz":
        return (tz_scenario(arg),)
    if sep and kind == "locale":
        return (locale_scenario(arg),)
    if sep and kind == "env-unset":
        if not arg:
            raise ValueError("env-unset requires a variable name: env-unset:<NAME>")
        return (env_unset_scenario(arg, environ=environ),)
    raise ValueError(f"unknown scenario {item!r}; expected one of: {_SCENARIO_HINT}")


def parse_scenario_spec(
    spec: str,
    *,
    environ: Mapping[str, str] | None = None,
    host_offset: int | None = None,
    host_is_utf8: bool | None = None,
) -> tuple[Scenario, ...]:
    """Parse a comma-separated scenario specification into scenarios.

    Duplicate scenario names are removed, keeping the first occurrence and the order.

    Raises:
        ValueError: If the spec has no tokens or any token is invalid.
    """
    tokens = [part.strip() for part in spec.split(",")]
    tokens = [token for token in tokens if token]
    if not tokens:
        raise ValueError(f"scenario spec is empty; expected one or more of: {_SCENARIO_HINT}")
    collected: list[Scenario] = []
    for token in tokens:
        collected.extend(
            _expand_item(token, environ=environ, host_offset=host_offset, host_is_utf8=host_is_utf8)
        )
    seen: set[str] = set()
    unique: list[Scenario] = []
    for scenario in collected:
        if scenario.name in seen:
            continue
        seen.add(scenario.name)
        unique.append(scenario)
    return tuple(unique)
