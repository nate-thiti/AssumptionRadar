"""Tests for the scenario catalog (P1-14): it must stay in sync with the scenarios."""

from __future__ import annotations

from assumptionradar.analysis.scenarios import (
    LOCALES,
    TZ_ZONES,
    default_scenarios,
    parse_scenario_spec,
)
from assumptionradar.core.catalog import (
    DEFAULT_SET_NOTE,
    SCENARIO_CATALOG,
    ScenarioInfo,
    find_scenario_info,
)
from assumptionradar.reporting.terminal import FIX_HINTS


def family(name: str) -> str:
    return name.split(":", 1)[0]


def test_families_are_unique_and_match_real_scenarios() -> None:
    families = [info.family for info in SCENARIO_CATALOG]
    assert len(families) == len(set(families))
    spec = "cwd,tmpdir,tz:+9,encoding,locale:de_DE,env-unset:AR_CATALOG_X"
    real = {family(s.name) for s in parse_scenario_spec(spec, environ={"AR_CATALOG_X": "1"})}
    assert set(families) == real


def test_syntax_lists_every_zone_and_locale() -> None:
    tz = find_scenario_info("tz")
    loc = find_scenario_info("locale")
    assert tz is not None
    assert loc is not None
    for zone in TZ_ZONES:
        assert zone in tz.syntax
    for name in LOCALES:
        assert name in loc.syntax


def test_default_note_matches_default_set() -> None:
    families: list[str] = []
    for scenario in default_scenarios(host_offset=7 * 3600, host_is_utf8=True):
        if family(scenario.name) not in families:
            families.append(family(scenario.name))
    assert "'default' expands to: " + ", ".join(families) == DEFAULT_SET_NOTE


def test_find_by_family_or_full_name() -> None:
    assert find_scenario_info("tz") is find_scenario_info("tz:+9")
    assert find_scenario_info("env-unset:HOME") is not None
    assert find_scenario_info("nope") is None
    assert find_scenario_info("") is None


def test_terminal_fix_hints_come_from_catalog() -> None:
    assert dict(FIX_HINTS) == {info.family: info.fix for info in SCENARIO_CATALOG}


def test_entries_are_complete() -> None:
    for info in SCENARIO_CATALOG:
        assert info.summary.endswith(".")
        assert info.reveals.endswith(".")
        assert not info.fix.endswith(".")  # rendered after "fix: " as a phrase
        assert set(info.to_dict()) == {"family", "syntax", "summary", "reveals", "fix", "notes"}


def test_to_dict() -> None:
    info = ScenarioInfo("x", "x", "S.", "R.", "f", ("n1",))
    assert info.to_dict() == {
        "family": "x",
        "syntax": "x",
        "summary": "S.",
        "reveals": "R.",
        "fix": "f",
        "notes": ["n1"],
    }
