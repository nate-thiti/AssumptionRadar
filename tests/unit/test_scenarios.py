"""Contract tests for assumptionradar.analysis.scenarios (ADR-0003, Spike 0001)."""

from __future__ import annotations

import pytest

from assumptionradar.analysis.scenarios import (
    LOCALES,
    REFERENCE_TIMESTAMP,
    TZ_ZONES,
    cwd_scenario,
    default_scenarios,
    default_tz_scenarios,
    encoding_scenario,
    env_unset_scenario,
    locale_scenario,
    parse_scenario_spec,
    tmpdir_scenario,
    tz_scenario,
)


def test_reference_timestamp() -> None:
    # 2023-11-14T22:13:20Z: outside northern-hemisphere DST, so EST5EDT -> -5h.
    assert REFERENCE_TIMESTAMP == 1_700_000_000


class TestCwd:
    def test_shape(self) -> None:
        s = cwd_scenario()
        assert s.name == "cwd"
        assert s.isolated_cwd is True
        assert dict(s.expect) == {"cwd": "{isolated_cwd}"}
        assert s.supported


class TestTmpdir:
    def test_shape(self) -> None:
        s = tmpdir_scenario()
        assert s.name == "tmpdir"
        assert s.isolated_tmp is True
        assert dict(s.expect) == {"tempdir": "{isolated_tmp}"}


class TestTz:
    def test_zone_table_uses_posix_strings_only(self) -> None:
        # Spike 0001: IANA names silently become UTC on Windows.
        assert TZ_ZONES == {"+9": ("JST-9", 32400), "-5": ("EST5EDT", -18000), "utc": ("UTC0", 0)}
        for posix, _ in TZ_ZONES.values():
            assert "/" not in posix

    @pytest.mark.parametrize(
        ("zone", "posix", "offset"),
        [("+9", "JST-9", 32400), ("-5", "EST5EDT", -18000), ("utc", "UTC0", 0)],
    )
    def test_scenario(self, zone: str, posix: str, offset: int) -> None:
        s = tz_scenario(zone)
        assert s.name == f"tz:{zone}"
        assert dict(s.env_set) == {"TZ": posix}
        assert dict(s.expect) == {"utc_offset_seconds": str(offset)}

    def test_unknown_zone(self) -> None:
        with pytest.raises(ValueError, match=r"\+9, -5, utc"):
            tz_scenario("Asia/Tokyo")

    @pytest.mark.parametrize(
        ("host", "names"),
        [
            (25200, ["tz:+9", "tz:-5"]),  # UTC+7 host
            (32400, ["tz:-5", "tz:utc"]),  # host already +9
            (-18000, ["tz:+9", "tz:utc"]),
            (0, ["tz:+9", "tz:-5"]),
        ],
    )
    def test_defaults_skip_host_offset(self, host: int, names: list[str]) -> None:
        assert [s.name for s in default_tz_scenarios(host_offset=host)] == names

    def test_defaults_without_argument_use_local_offset(self) -> None:
        assert len(default_tz_scenarios()) == 2


class TestEncoding:
    def test_non_utf8_host_switches_to_utf8(self) -> None:
        s = encoding_scenario(host_is_utf8=False)
        assert s.name == "encoding:utf-8"
        assert dict(s.env_set) == {"PYTHONUTF8": "1"}
        assert dict(s.expect) == {"preferred_encoding_utf8": "true"}

    def test_utf8_host_switches_to_legacy(self) -> None:
        s = encoding_scenario(host_is_utf8=True)
        assert s.name == "encoding:legacy"
        assert dict(s.env_set) == {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        assert dict(s.expect) == {"preferred_encoding_utf8": "false"}

    def test_default_detects_host(self) -> None:
        assert encoding_scenario().name in {"encoding:utf-8", "encoding:legacy"}


class TestLocale:
    def test_table(self) -> None:
        assert set(LOCALES) == {"de_DE", "fr_FR", "tr_TR"}

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [("win32", "de-DE"), ("linux", "de_DE.UTF-8"), ("darwin", "de_DE.UTF-8")],
    )
    def test_platform_specific_value(self, platform: str, expected: str) -> None:
        s = locale_scenario("de_DE", platform=platform)
        assert s.name == "locale:de_DE"
        assert s.child_locale == expected
        assert dict(s.expect) == {"locale_applied": "true"}
        assert dict(s.env_set) == {}

    def test_unknown(self) -> None:
        with pytest.raises(ValueError, match="de_DE, fr_FR, tr_TR"):
            locale_scenario("xx_XX")


class TestEnvUnset:
    def test_present_variable(self) -> None:
        s = env_unset_scenario("API_URL", environ={"API_URL": "secret-value"})
        assert s.name == "env-unset:API_URL"
        assert s.env_unset == ("API_URL",)
        assert dict(s.expect) == {"env:API_URL": "absent"}
        assert s.supported
        assert "secret-value" not in repr(s.to_dict())

    def test_absent_variable_is_unsupported(self) -> None:
        s = env_unset_scenario("API_URL", environ={})
        assert not s.supported
        assert s.unsupported_reason is not None
        assert "not set" in s.unsupported_reason

    def test_invalid_name(self) -> None:
        with pytest.raises(ValueError):
            env_unset_scenario("A=B", environ={})


class TestDefaults:
    def test_default_set(self) -> None:
        names = [s.name for s in default_scenarios(host_offset=25200, host_is_utf8=False)]
        assert names == ["cwd", "tz:+9", "tz:-5", "encoding:utf-8", "tmpdir"]


class TestParseSpec:
    def test_single(self) -> None:
        assert [s.name for s in parse_scenario_spec("cwd")] == ["cwd"]

    def test_many_with_spaces(self) -> None:
        out = parse_scenario_spec(" cwd , tz:+9,tmpdir ", environ={})
        assert [s.name for s in out] == ["cwd", "tz:+9", "tmpdir"]

    def test_tz_alone_expands_to_defaults(self) -> None:
        assert [s.name for s in parse_scenario_spec("tz", host_offset=0)] == ["tz:+9", "tz:-5"]

    def test_default_keyword(self) -> None:
        out = parse_scenario_spec("default", host_offset=25200, host_is_utf8=False)
        assert [s.name for s in out] == ["cwd", "tz:+9", "tz:-5", "encoding:utf-8", "tmpdir"]

    def test_encoding_locale_env(self) -> None:
        out = parse_scenario_spec(
            "encoding,locale:tr_TR,env-unset:HOME_X", environ={"HOME_X": "1"}, host_is_utf8=True
        )
        assert [s.name for s in out] == ["encoding:legacy", "locale:tr_TR", "env-unset:HOME_X"]

    def test_duplicates_removed_keep_first(self) -> None:
        out = parse_scenario_spec("cwd,tz:+9,cwd,tz", host_offset=0)
        assert [s.name for s in out] == ["cwd", "tz:+9", "tz:-5"]

    @pytest.mark.parametrize(
        "spec", ["", " , ", "nope", "tz:+3", "locale", "env-unset:", "locale:xx"]
    )
    def test_invalid(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_scenario_spec(spec, environ={})

    def test_error_lists_valid_forms(self) -> None:
        with pytest.raises(ValueError, match="env-unset:<NAME>"):
            parse_scenario_spec("nope")
