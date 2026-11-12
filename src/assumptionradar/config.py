"""Configuration read from ``[tool.assumptionradar]`` or ``assumptionradar.toml``.

A standalone ``assumptionradar.toml`` wins over ``pyproject.toml`` in the same
directory. Discovery walks up from the project root and stops at the first
directory that contains ``.git``. Every problem with a configuration file is
reported as a :class:`ConfigurationError` that names the file.
"""

from __future__ import annotations

import difflib
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Final

import assumptionradar
from assumptionradar.core.exceptions import ConfigurationError
from assumptionradar.static.cache import FileCache
from assumptionradar.static.engine import Rule, ScanResult, scan
from assumptionradar.static.project import ProjectInfo, load_project
from assumptionradar.static.rules import (
    OptionalImportRule,
    PathLookupRule,
    PlatformSpecificRule,
    RelativePathAccessRule,
    RequiredEnvVarRule,
    UndeclaredEnvVarRule,
)

KNOWN_RULE_IDS: Final = (
    "AR-CWD-001",
    "AR-ENV-001",
    "AR-ENV-002",
    "AR-EXE-001",
    "AR-IMP-001",
    "AR-PLAT-001",
)

_STANDALONE_NAME: Final = "assumptionradar.toml"
_PYPROJECT_NAME: Final = "pyproject.toml"
_REPO_MARKER: Final = ".git"
_TOOL_KEY: Final = "assumptionradar"
_COMMAND_LINE: Final = "command line"

# TOML key (hyphenated) -> Settings field name.
_FILE_KEYS: Final[dict[str, str]] = {
    "include": "include",
    "exclude": "exclude",
    "use-default-excludes": "use_default_excludes",
    "max-file-bytes": "max_file_bytes",
    "rules": "rules",
    "ignore-rules": "ignore_rules",
    "strict-suppressions": "strict_suppressions",
    "cache": "cache",
    "cache-dir": "cache_dir",
    "fail-on": "fail_on",
    "format": "format",
}
_ENV_KEYS: Final = ("declared",)

# Settings field name -> the name shown in error messages.
_NAMES: Final[dict[str, str]] = {
    "include": "include",
    "exclude": "exclude",
    "use_default_excludes": "use-default-excludes",
    "max_file_bytes": "max-file-bytes",
    "rules": "rules",
    "ignore_rules": "ignore-rules",
    "strict_suppressions": "strict-suppressions",
    "cache": "cache",
    "cache_dir": "cache-dir",
    "fail_on": "fail-on",
    "format": "format",
    "env_declared": "env.declared",
}
_LIST_FIELDS: Final = ("include", "exclude", "rules", "ignore_rules", "env_declared")
_BOOL_FIELDS: Final = ("use_default_excludes", "strict_suppressions", "cache")
_CHOICES: Final[dict[str, tuple[str, ...]]] = {
    "fail_on": ("info", "warning", "error"),
    "format": ("text", "json", "sarif"),
}


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated AssumptionRadar settings.

    Attributes:
        config_file: The file the settings were read from, or None for defaults.
        include: Glob patterns of files to scan.
        exclude: Glob patterns of files to skip.
        use_default_excludes: Whether the built-in exclude list applies.
        max_file_bytes: Files larger than this many bytes are skipped.
        rules: Rule ids to run, or None for every rule.
        ignore_rules: Rule ids to skip.
        strict_suppressions: Whether bare ``ignore`` comments are refused and reported.
        cache: Whether scan results are cached.
        cache_dir: Directory for the cache.
        fail_on: Lowest severity that makes the scan fail.
        format: Output format.
        env_declared: Environment variables the project declares, or None when
            the ``[env]`` table is absent.
    """

    config_file: Path | None = None
    include: tuple[str, ...] = ("**/*.py",)
    exclude: tuple[str, ...] = ()
    use_default_excludes: bool = True
    max_file_bytes: int = 1_000_000
    rules: tuple[str, ...] | None = None
    ignore_rules: tuple[str, ...] = ()
    strict_suppressions: bool = False
    cache: bool = True
    cache_dir: str = ".assumptionradar_cache"
    fail_on: str = "warning"
    format: str = "text"
    env_declared: tuple[str, ...] | None = None

    def with_overrides(self, **changes: object) -> Settings:
        """Return a copy with command-line values applied and validated.

        Args:
            **changes: Field names and their new values. ``None`` values are ignored.

        Returns:
            A new, validated settings object.

        Raises:
            TypeError: If a name is not a settings field.
            ConfigurationError: If a resulting value is invalid.
        """
        names = {field.name for field in fields(self)}
        raw: dict[str, object] = {name: getattr(self, name) for name in names}
        for key, value in changes.items():
            if key not in names:
                raise TypeError(f"Settings has no setting {key!r}")
            if value is not None:
                raw[key] = value
        return replace(self, **_validate(raw, _COMMAND_LINE))

    def to_dict(self) -> dict[str, object]:
        """Return the settings as a JSON-friendly dict keyed by field name.

        Returns:
            Field names as keys, ``config_file`` as a string or None, and tuples as lists.
        """
        out: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, tuple):
                value = list(value)
            elif isinstance(value, Path):
                value = str(value)
            out[field.name] = value
        return out


def _describe(value: object) -> str:
    """Return a value with its repr and type name, for error messages."""
    return f"{value!r} ({type(value).__name__})"


def _error(source: str, problem: str) -> ConfigurationError:
    """Build a configuration error that starts with its source."""
    return ConfigurationError(f"{source}: {problem}")


def _is_str_list(value: object) -> bool:
    """Return True when ``value`` is a list or tuple of strings."""
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)


def _normalize_lists(values: dict[str, Any], source: str) -> None:
    """Check the list-of-strings fields and convert them to tuples in place.

    Args:
        values: The raw values. Updated in place.
        source: The message prefix.

    Raises:
        ConfigurationError: If a list field is not a list of strings, or ``include`` is empty.
    """
    for field in _LIST_FIELDS:
        value = values.get(field)
        if value is None:
            continue
        if not _is_str_list(value):
            raise _error(
                source, f"{_NAMES[field]!r} must be a list of strings, got {_describe(value)}"
            )
        values[field] = tuple(value)
    if values.get("include") == ():
        raise _error(source, "'include' must not be empty")


def _check_scalars(values: Mapping[str, Any], source: str) -> None:
    """Check the boolean, integer and string fields.

    Args:
        values: The normalized values.
        source: The message prefix.

    Raises:
        ConfigurationError: If a scalar value is out of range or has the wrong type.
    """
    for field in _BOOL_FIELDS:
        value = values.get(field)
        if value is not None and not isinstance(value, bool):
            raise _error(source, f"{_NAMES[field]!r} must be true or false, got {_describe(value)}")

    max_bytes = values.get("max_file_bytes")
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1
    ):
        raise _error(
            source,
            f"'max-file-bytes' must be an integer >= 1, got {_describe(max_bytes)}",
        )

    cache_dir = values.get("cache_dir")
    if cache_dir is not None and (not isinstance(cache_dir, str) or not cache_dir):
        raise _error(
            source,
            f"'cache-dir' must be a non-empty string, got {_describe(cache_dir)}",
        )


def _check_choices(values: Mapping[str, Any], source: str) -> None:
    """Check the fields that accept one of a fixed set of strings.

    Args:
        values: The normalized values.
        source: The message prefix.

    Raises:
        ConfigurationError: If a value is not one of its allowed choices.
    """
    for field, choices in _CHOICES.items():
        value = values.get(field)
        if value is None or value in choices:
            continue
        hint = ""
        if isinstance(value, str):
            close = difflib.get_close_matches(value, choices, n=1)
            if close:
                hint = f"; did you mean {close[0]!r}?"
        options = ", ".join(repr(choice) for choice in choices)
        raise _error(
            source,
            f"{_NAMES[field]!r} must be one of {options}, got {value!r}{hint}",
        )


def _check_rule_ids(values: Mapping[str, Any], source: str) -> None:
    """Check that ``rules`` and ``ignore_rules`` only name known rule ids.

    Args:
        values: The normalized values.
        source: The message prefix.

    Raises:
        ConfigurationError: If a rule id is unknown.
    """
    for field in ("rules", "ignore_rules"):
        ids = values.get(field)
        if ids is None:
            continue
        for rid in ids:
            if rid in KNOWN_RULE_IDS:
                continue
            close = difflib.get_close_matches(rid, KNOWN_RULE_IDS, n=1)
            if close:
                tail = f"did you mean {close[0]!r}?"
            else:
                tail = "known rules: " + ", ".join(KNOWN_RULE_IDS)
            raise _error(source, f"{_NAMES[field]!r} contains unknown rule id {rid!r}; {tail}")


def _validate(raw: Mapping[str, object], source: str) -> dict[str, Any]:
    """Check raw settings values and normalize lists to tuples.

    Args:
        raw: Values keyed by field name. Missing keys and ``None`` values are left alone.
        source: The file path or ``"command line"`` used as the message prefix.

    Returns:
        The normalized values, keyed by field name.

    Raises:
        ConfigurationError: If any value is invalid.
    """
    values: dict[str, Any] = dict(raw)
    _normalize_lists(values, source)
    _check_scalars(values, source)
    _check_choices(values, source)
    _check_rule_ids(values, source)
    return values


def _read_document(path: Path) -> dict[str, Any]:
    """Parse a TOML file.

    Args:
        path: The file to read.

    Returns:
        The parsed document.

    Raises:
        ConfigurationError: If the file cannot be read or is not valid TOML.
    """
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"{path}: cannot read file: {exc}") from exc
    return data


def _claims_pyproject(path: Path) -> bool:
    """Return True when a ``pyproject.toml`` has a ``tool.assumptionradar`` key.

    An unparsable file counts as a claim so that its error is reported later.

    Args:
        path: The ``pyproject.toml`` to check.

    Returns:
        Whether this file is the configuration source.
    """
    try:
        data = _read_document(path)
    except ConfigurationError:
        return True
    tool = data.get("tool")
    return isinstance(tool, dict) and _TOOL_KEY in tool


def find_config_file(start: Path) -> Path | None:
    """Find the configuration file that applies to ``start``.

    Args:
        start: The directory to search from. Its parents are searched in turn.

    Returns:
        The standalone ``assumptionradar.toml`` or the ``pyproject.toml`` with a
        ``tool.assumptionradar`` key in the nearest directory, or None. The search
        stops after the first directory that contains ``.git``.
    """
    start = start.absolute()  # a relative path such as "." has no parents to walk
    for directory in [start, *start.parents]:
        standalone = directory / _STANDALONE_NAME
        if standalone.is_file():
            return standalone
        pyproject = directory / _PYPROJECT_NAME
        if pyproject.is_file() and _claims_pyproject(pyproject):
            return pyproject
        if (directory / _REPO_MARKER).exists():
            return None
    return None


def _unknown_key(path: Path, key: str, label: str, valid: Sequence[str]) -> ConfigurationError:
    """Build the error for an unknown key, with a suggestion when one is close."""
    head = f"{path}: unknown key {key!r} in {label}"
    close = difflib.get_close_matches(key, valid, n=1)
    if close:
        return ConfigurationError(f"{head}; did you mean {close[0]!r}?")
    return ConfigurationError(f"{head}; valid keys: " + ", ".join(sorted(valid)))


def _file_table(path: Path, data: dict[str, Any]) -> tuple[dict[Any, Any], str, str]:
    """Return the settings table of a document and the labels used in messages.

    Args:
        path: The file the document came from.
        data: The parsed document.

    Returns:
        The settings table, the label of the main table, and the label of ``[env]``.

    Raises:
        ConfigurationError: If the table is missing or is not a table.
    """
    if path.name == _PYPROJECT_NAME:
        tool = data.get("tool")
        if not isinstance(tool, dict) or _TOOL_KEY not in tool:
            raise ConfigurationError(f"{path}: no [tool.{_TOOL_KEY}] table")
        table: object = tool[_TOOL_KEY]
        label = f"[tool.{_TOOL_KEY}]"
        env_label = f"[tool.{_TOOL_KEY}.env]"
    else:
        table = data
        label = "the config file"
        env_label = "[env]"
    if not isinstance(table, dict):
        raise ConfigurationError(f"{path}: {label} must be a table, got {_describe(table)}")
    return table, label, env_label


def _env_table(path: Path, table: dict[Any, Any], env_label: str) -> dict[Any, Any]:
    """Return the ``env`` sub-table, or an empty dict when it is absent.

    Args:
        path: The file the table came from.
        table: The settings table.
        env_label: The label of the ``env`` table used in messages.

    Returns:
        The ``env`` table.

    Raises:
        ConfigurationError: If ``env`` is present but is not a table.
    """
    if "env" not in table:
        return {}
    env = table["env"]
    if not isinstance(env, dict):
        raise ConfigurationError(f"{path}: {env_label} must be a table, got {_describe(env)}")
    return env


def _from_file(path: Path) -> Settings:
    """Read and validate the settings in one configuration file.

    Args:
        path: A ``pyproject.toml`` or a standalone TOML file.

    Returns:
        The validated settings with ``config_file`` set to ``path``.

    Raises:
        ConfigurationError: If the file is invalid in any way.
    """
    table, label, env_label = _file_table(path, _read_document(path))
    env = _env_table(path, table, env_label)

    valid_keys = (*_FILE_KEYS, "env")
    for key in table:
        if key not in valid_keys:
            raise _unknown_key(path, key, label, valid_keys)
    for key in env:
        if key not in _ENV_KEYS:
            raise _unknown_key(path, key, env_label, _ENV_KEYS)

    raw: dict[str, object] = {}
    for key, value in table.items():
        if key != "env":
            raw[_FILE_KEYS[key]] = value
    if "declared" in env:
        raw["env_declared"] = env["declared"]
    return replace(Settings(config_file=path), **_validate(raw, str(path)))


def load_settings(root: Path, config_path: Path | None = None) -> Settings:
    """Load the settings for a project.

    Args:
        root: The project root, used to discover a configuration file.
        config_path: An explicit configuration file. When given, discovery is skipped.

    Returns:
        The validated settings. Defaults are used when no file is found.

    Raises:
        ConfigurationError: If the explicit file is missing or any file is invalid.
    """
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigurationError(f"{config_path}: config file not found")
        return _from_file(config_path)
    found = find_config_file(root)
    if found is None:
        return Settings()
    return _from_file(found)


def rules_from_settings(settings: Settings, project: ProjectInfo) -> tuple[Rule, ...]:
    """Return the rule instances that the settings and project enable.

    Args:
        settings: The validated settings.
        project: The project metadata. The optional-import rule needs a ``pyproject.toml``.

    Returns:
        Fresh rule instances in a fixed order, after applying ``rules`` and ``ignore_rules``.
    """
    candidates: list[Rule] = [RelativePathAccessRule(), RequiredEnvVarRule()]
    if settings.env_declared is not None or (
        settings.rules is not None and "AR-ENV-002" in settings.rules
    ):
        candidates.append(UndeclaredEnvVarRule(settings.env_declared or ()))
    candidates.append(PathLookupRule())
    candidates.append(PlatformSpecificRule())
    if project.has_pyproject:
        candidates.append(OptionalImportRule(project.declared, project.first_party))

    selected = [
        rule
        for rule in candidates
        if (settings.rules is None or rule.rule_id in settings.rules)
        and rule.rule_id not in settings.ignore_rules
    ]
    return tuple(selected)


def scan_with_settings(root: Path, settings: Settings) -> ScanResult:
    """Scan a project root using the rules and options from ``settings``.

    Args:
        root: The project root directory.
        settings: The validated settings that select rules, files and caching.

    Returns:
        The scan result from :func:`assumptionradar.static.engine.scan`.
    """
    rules = rules_from_settings(settings, load_project(root))
    cache = (
        FileCache(root / settings.cache_dir, f"static-{assumptionradar.__version__}")
        if settings.cache
        else None
    )
    return scan(
        root,
        rules,
        include=settings.include,
        exclude=settings.exclude,
        use_default_excludes=settings.use_default_excludes,
        max_file_bytes=settings.max_file_bytes,
        cache=cache,
        strict_suppressions=settings.strict_suppressions,
    )
