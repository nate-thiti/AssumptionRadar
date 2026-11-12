"""Tests for [tool.assumptionradar] configuration (P2-8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from assumptionradar.config import (
    KNOWN_RULE_IDS,
    Settings,
    find_config_file,
    load_settings,
    rules_from_settings,
)
from assumptionradar.core.exceptions import ConfigurationError
from assumptionradar.static.project import load_project


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def pyproject(root: Path, body: str) -> Path:
    return write(root / "pyproject.toml", "[project]\nname = 'x'\n\n" + body)


def error_for(root: Path, body: str) -> str:
    pyproject(root, body)
    with pytest.raises(ConfigurationError) as info:
        load_settings(root)
    return str(info.value)


# --------------------------------------------------------------------- defaults


def test_defaults_without_any_config(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    settings = load_settings(tmp_path)
    assert settings == Settings()
    assert settings.config_file is None
    assert settings.include == ("**/*.py",)
    assert settings.exclude == ()
    assert settings.use_default_excludes is True
    assert settings.max_file_bytes == 1_000_000
    assert settings.rules is None
    assert settings.ignore_rules == ()
    assert settings.strict_suppressions is False
    assert settings.cache is True
    assert settings.cache_dir == ".assumptionradar_cache"
    assert settings.fail_on == "warning"
    assert settings.format == "text"
    assert settings.env_declared is None


def test_known_rule_ids() -> None:
    assert KNOWN_RULE_IDS == (
        "AR-CWD-001",
        "AR-ENV-001",
        "AR-ENV-002",
        "AR-EXE-001",
        "AR-IMP-001",
        "AR-PLAT-001",
    )


# --------------------------------------------------------------------- reading


def test_full_pyproject_config(tmp_path: Path) -> None:
    path = pyproject(
        tmp_path,
        "[tool.assumptionradar]\n"
        'include = ["src/**/*.py", "tests/**/*.py"]\n'
        'exclude = ["tests/fixtures/**"]\n'
        "use-default-excludes = false\n"
        "max-file-bytes = 5000\n"
        'rules = ["AR-CWD-001", "AR-ENV-001"]\n'
        'ignore-rules = ["AR-ENV-001"]\n'
        "strict-suppressions = true\n"
        "cache = false\n"
        'cache-dir = ".cache/ar"\n'
        'fail-on = "error"\n'
        'format = "json"\n'
        "\n[tool.assumptionradar.env]\n"
        'declared = ["DATABASE_URL", "HOME"]\n',
    )
    settings = load_settings(tmp_path)
    assert settings == Settings(
        config_file=path,
        include=("src/**/*.py", "tests/**/*.py"),
        exclude=("tests/fixtures/**",),
        use_default_excludes=False,
        max_file_bytes=5000,
        rules=("AR-CWD-001", "AR-ENV-001"),
        ignore_rules=("AR-ENV-001",),
        strict_suppressions=True,
        cache=False,
        cache_dir=".cache/ar",
        fail_on="error",
        format="json",
        env_declared=("DATABASE_URL", "HOME"),
    )


def test_pyproject_without_tool_table_uses_defaults(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    pyproject(tmp_path, "[tool.other]\nx = 1\n")
    assert load_settings(tmp_path) == Settings()
    assert find_config_file(tmp_path) is None


def test_standalone_file_wins_over_pyproject(tmp_path: Path) -> None:
    pyproject(tmp_path, '[tool.assumptionradar]\nformat = "json"\n')
    standalone = write(tmp_path / "assumptionradar.toml", 'format = "sarif"\n')
    assert find_config_file(tmp_path) == standalone
    settings = load_settings(tmp_path)
    assert (settings.config_file, settings.format) == (standalone, "sarif")


def test_discovery_walks_up_to_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    path = pyproject(repo, '[tool.assumptionradar]\nfail-on = "info"\n')
    nested = repo / "pkg" / "sub"
    nested.mkdir(parents=True)
    assert find_config_file(nested) == path
    assert load_settings(nested).fail_on == "info"


def test_discovery_walks_up_from_a_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    pyproject(repo, '[tool.assumptionradar]\nfail-on = "info"\n')
    nested = repo / "pkg"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert load_settings(Path()).fail_on == "info"


def test_discovery_stops_at_repo_root(tmp_path: Path) -> None:
    pyproject(tmp_path, '[tool.assumptionradar]\nfail-on = "info"\n')
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert find_config_file(repo) is None
    assert load_settings(repo) == Settings()


def test_explicit_config_path(tmp_path: Path) -> None:
    custom = write(tmp_path / "conf" / "ar.toml", 'exclude = ["build/**"]\n')
    settings = load_settings(tmp_path, config_path=custom)
    assert (settings.config_file, settings.exclude) == (custom, ("build/**",))
    other = pyproject(tmp_path / "elsewhere", '[tool.assumptionradar]\nformat = "json"\n')
    assert load_settings(tmp_path, config_path=other).format == "json"


def test_explicit_pyproject_without_table_is_an_error(tmp_path: Path) -> None:
    path = pyproject(tmp_path, "")
    with pytest.raises(ConfigurationError, match=r"no \[tool\.assumptionradar\] table"):
        load_settings(tmp_path, config_path=path)


def test_missing_explicit_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="config file not found"):
        load_settings(tmp_path, config_path=tmp_path / "nope.toml")


# --------------------------------------------------------------------- errors


def test_invalid_toml(tmp_path: Path) -> None:
    message = error_for(tmp_path, "[tool.assumptionradar\nformat = ")
    assert message.startswith(f"{tmp_path / 'pyproject.toml'}: invalid TOML: ")


def test_unknown_key_suggests_close_match(tmp_path: Path) -> None:
    message = error_for(tmp_path, '[tool.assumptionradar]\nexcludes = ["x"]\n')
    assert message == (
        f"{tmp_path / 'pyproject.toml'}: unknown key 'excludes' in [tool.assumptionradar]; "
        "did you mean 'exclude'?"
    )


def test_unknown_key_without_close_match_lists_keys(tmp_path: Path) -> None:
    message = error_for(tmp_path, "[tool.assumptionradar]\nbanana = 1\n")
    assert "unknown key 'banana' in [tool.assumptionradar]; valid keys: " in message
    assert "cache-dir" in message
    assert "max-file-bytes" in message


def test_unknown_env_key(tmp_path: Path) -> None:
    message = error_for(tmp_path, "[tool.assumptionradar.env]\ndeclare = []\n")
    assert message.endswith(
        "unknown key 'declare' in [tool.assumptionradar.env]; did you mean 'declared'?"
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('include = "src"', "'include' must be a list of strings, got 'src' (str)"),
        ("exclude = [1, 2]", "'exclude' must be a list of strings, got [1, 2] (list)"),
        (
            'use-default-excludes = "yes"',
            "'use-default-excludes' must be true or false, got 'yes' (str)",
        ),
        ("max-file-bytes = 0", "'max-file-bytes' must be an integer >= 1, got 0 (int)"),
        ("max-file-bytes = true", "'max-file-bytes' must be an integer >= 1, got True (bool)"),
        ('max-file-bytes = "1MB"', "'max-file-bytes' must be an integer >= 1, got '1MB' (str)"),
        ("strict-suppressions = 1", "'strict-suppressions' must be true or false, got 1 (int)"),
        ("cache = 0", "'cache' must be true or false, got 0 (int)"),
        ('cache-dir = ""', "'cache-dir' must be a non-empty string, got '' (str)"),
        ("cache-dir = 3", "'cache-dir' must be a non-empty string, got 3 (int)"),
        (
            'fail-on = "warn"',
            "'fail-on' must be one of 'info', 'warning', 'error', got 'warn'; "
            "did you mean 'warning'?",
        ),
        ('format = "yaml"', "'format' must be one of 'text', 'json', 'sarif', got 'yaml'"),
        ('rules = "AR-CWD-001"', "'rules' must be a list of strings, got 'AR-CWD-001' (str)"),
        ("include = []", "'include' must not be empty"),
    ],
)
def test_value_errors(tmp_path: Path, body: str, expected: str) -> None:
    message = error_for(tmp_path, f"[tool.assumptionradar]\n{body}\n")
    assert message == f"{tmp_path / 'pyproject.toml'}: {expected}"


def test_unknown_rule_ids(tmp_path: Path) -> None:
    message = error_for(tmp_path, '[tool.assumptionradar]\nrules = ["AR-CWD-01"]\n')
    assert message == (
        f"{tmp_path / 'pyproject.toml'}: 'rules' contains unknown rule id 'AR-CWD-01'; "
        "did you mean 'AR-CWD-001'?"
    )
    message = error_for(tmp_path, '[tool.assumptionradar]\nignore-rules = ["NOPE"]\n')
    assert message.endswith(
        "'ignore-rules' contains unknown rule id 'NOPE'; known rules: " + ", ".join(KNOWN_RULE_IDS)
    )


def test_table_shapes(tmp_path: Path) -> None:
    assert error_for(tmp_path, '[tool]\nassumptionradar = "on"\n').endswith(
        "[tool.assumptionradar] must be a table, got 'on' (str)"
    )
    assert error_for(tmp_path, "[tool.assumptionradar]\nenv = 1\n").endswith(
        "[tool.assumptionradar.env] must be a table, got 1 (int)"
    )
    assert error_for(tmp_path, "[tool.assumptionradar.env]\ndeclared = 'HOME'\n").endswith(
        "'env.declared' must be a list of strings, got 'HOME' (str)"
    )


# ------------------------------------------------------------------ overrides


def test_overrides_take_precedence_and_none_is_ignored(tmp_path: Path) -> None:
    pyproject(tmp_path, '[tool.assumptionradar]\nformat = "json"\nfail-on = "error"\n')
    settings = load_settings(tmp_path).with_overrides(format="sarif", fail_on=None)
    assert (settings.format, settings.fail_on) == ("sarif", "error")


def test_overrides_are_validated() -> None:
    with pytest.raises(ConfigurationError) as info:
        Settings().with_overrides(fail_on="loud")
    assert str(info.value) == (
        "command line: 'fail-on' must be one of 'info', 'warning', 'error', got 'loud'"
    )
    with pytest.raises(ConfigurationError, match="unknown rule id 'AR-X-1'"):
        Settings().with_overrides(rules=("AR-X-1",))
    with pytest.raises(TypeError):
        Settings().with_overrides(colour="red")


# -------------------------------------------------------------- rule selection


def ids(settings: Settings, root: Path) -> list[str]:
    return [rule.rule_id for rule in rules_from_settings(settings, load_project(root))]


def test_rule_selection(tmp_path: Path) -> None:
    assert ids(Settings(), tmp_path) == ["AR-CWD-001", "AR-ENV-001", "AR-EXE-001", "AR-PLAT-001"]
    pyproject(tmp_path, "")
    assert ids(Settings(), tmp_path)[-1] == "AR-IMP-001"
    assert ids(Settings(ignore_rules=("AR-PLAT-001", "AR-IMP-001")), tmp_path) == [
        "AR-CWD-001",
        "AR-ENV-001",
        "AR-EXE-001",
    ]
    assert ids(Settings(rules=("AR-EXE-001", "AR-CWD-001")), tmp_path) == [
        "AR-CWD-001",
        "AR-EXE-001",
    ]


def test_env_declared_enables_env_002(tmp_path: Path) -> None:
    rules = rules_from_settings(Settings(env_declared=("HOME",)), load_project(tmp_path))
    [env002] = [r for r in rules if r.rule_id == "AR-ENV-002"]
    assert "AR-ENV-002" in ids(Settings(rules=("AR-ENV-002",)), tmp_path)
    assert env002.version != "1"  # the declared list is part of the cache signature


def test_imp_needs_a_pyproject_even_when_selected(tmp_path: Path) -> None:
    assert ids(Settings(rules=("AR-IMP-001",)), tmp_path) == []
