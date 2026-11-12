"""Command-line interface for AssumptionRadar."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from collections.abc import Callable, Sequence
from enum import IntEnum
from pathlib import Path
from typing import Any, TextIO

from assumptionradar import __version__
from assumptionradar.baseline import (
    DEFAULT_BASELINE_NAME,
    Baseline,
    compare,
    load_baseline,
    write_baseline,
)
from assumptionradar.config import Settings, load_settings, rules_from_settings, scan_with_settings
from assumptionradar.core.catalog import (
    DEFAULT_SET_NOTE,
    SCENARIO_CATALOG,
    find_scenario_info,
)
from assumptionradar.core.exceptions import BaselineError, ConfigurationError
from assumptionradar.core.models import Severity
from assumptionradar.reporting.sarif import sarif_from_scan
from assumptionradar.reporting.scan_report import build_scan_report, is_failing, render_scan
from assumptionradar.reporting.terminal import render_report, to_text, use_color
from assumptionradar.static.engine import ScanResult
from assumptionradar.static.project import load_project

_DESCRIPTION = "Discover hidden environmental assumptions before they break your software."
_REPORT_SCHEMA = "1"
_REPORT_KEYS = (
    "schema_version",
    "tool",
    "platform",
    "observation",
    "verifications",
    "verification_skipped",
)


class ExitCode(IntEnum):
    """Process exit codes returned by the command-line interface.

    Attributes:
        OK: Success: no verified regression and nothing inconclusive.
        FINDINGS: At least one verified test has a regression, or a scan or baseline
            comparison found a finding at or above the ``fail-on`` severity.
        USAGE: Bad arguments, an unknown scenario, or an unreadable or invalid report.
        INTERNAL: An unexpected error inside AssumptionRadar.
        INCONCLUSIVE: No regression, but at least one test is inconclusive or errored.
    """

    OK = 0
    FINDINGS = 1
    USAGE = 2
    INTERNAL = 3
    INCONCLUSIVE = 4


def emit(text: str, stream: TextIO) -> None:
    """Write text to a stream, replacing characters the stream cannot encode.

    Args:
        text: The text to write.
        stream: The stream to write to, typically ``sys.stdout`` or ``sys.stderr``.
    """
    try:
        stream.write(text)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(enc, "replace").decode(enc))


def _add_scan_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``scan`` subcommand.

    Args:
        commands: The subparsers action of the top-level parser.
    """
    scan_parser = commands.add_parser("scan", help="scan a project for environment assumptions")
    scan_parser.add_argument("root", nargs="?", default=".", help="project root (default: current)")
    scan_parser.add_argument(
        "--config", default=None, help="use this config file instead of discovery"
    )
    scan_parser.add_argument(
        "--format",
        choices=("text", "json", "sarif"),
        default=None,
        help="output format (default: from config, else text)",
    )
    scan_parser.add_argument(
        "-o", "--output", default=None, help="write the report to this file instead of stdout"
    )
    verbosity = scan_parser.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="omit the summary line")
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="show fixes, suppressed and skipped files"
    )
    scan_parser.add_argument(
        "--fail-on",
        choices=("info", "warning", "error"),
        default=None,
        help="lowest finding severity that fails (default: from config, else warning)",
    )
    scan_parser.add_argument(
        "--strict-suppressions",
        action="store_true",
        help="refuse bare suppression comments and report them",
    )
    scan_parser.add_argument(
        "--no-cache", action="store_true", help="do not read or write the result cache"
    )
    scan_parser.add_argument(
        "--exit-zero", action="store_true", help="always exit 0 after a successful scan"
    )
    scan_parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")


def _add_doctor_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``doctor`` subcommand.

    Args:
        commands: The subparsers action of the top-level parser.
    """
    doctor = commands.add_parser("doctor", help="show the environment and configuration")
    doctor.add_argument("root", nargs="?", default=".", help="project root (default: current)")
    doctor.add_argument("--config", default=None, help="use this config file instead of discovery")
    doctor.add_argument("--format", choices=("text", "json"), default="text")


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="assumptionradar", description=_DESCRIPTION)
    parser.add_argument(
        "--version",
        action="version",
        version=f"assumptionradar {__version__}",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    scenarios = commands.add_parser("scenarios", help="list the verification scenarios")
    scenarios.add_argument("--format", choices=("text", "json"), default="text")

    explain = commands.add_parser("explain", help="describe one verification scenario")
    explain.add_argument("name", help="a scenario family or full name, such as tz or tz:+9")
    explain.add_argument("--format", choices=("text", "json"), default="text")

    report = commands.add_parser("report", help="work with saved reports")
    report_commands = report.add_subparsers(dest="report_command", required=True, metavar="ACTION")
    show = report_commands.add_parser("show", help="render a saved JSON report")
    show.add_argument("path", help="path to a report written by --assumption-report")
    show.add_argument("--format", choices=("text", "json"), default="text")
    show.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    show.add_argument(
        "--exit-zero",
        action="store_true",
        help="always exit 0 after a successful read",
    )
    config = commands.add_parser("config", help="work with the AssumptionRadar configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True, metavar="ACTION")
    check = config_commands.add_parser("check", help="validate the configuration")
    check.add_argument("root", nargs="?", default=".", help="project root (default: current)")
    check.add_argument("--config", default=None, help="use this config file instead of discovery")
    check.add_argument("--format", choices=("text", "json"), default="text")
    baseline_parser = commands.add_parser("baseline", help="record and compare known findings")
    baseline_commands = baseline_parser.add_subparsers(
        dest="baseline_command", required=True, metavar="ACTION"
    )
    baseline_create = baseline_commands.add_parser("create", help="record the current findings")
    baseline_create.add_argument(
        "root", nargs="?", default=".", help="project root (default: current)"
    )
    baseline_create.add_argument(
        "--config", default=None, help="use this config file instead of discovery"
    )
    baseline_create.add_argument(
        "--output",
        default=None,
        help=f"baseline file to write (default: ROOT/{DEFAULT_BASELINE_NAME})",
    )
    baseline_compare = baseline_commands.add_parser(
        "compare", help="compare the current findings with a baseline"
    )
    baseline_compare.add_argument(
        "root", nargs="?", default=".", help="project root (default: current)"
    )
    baseline_compare.add_argument(
        "--config", default=None, help="use this config file instead of discovery"
    )
    baseline_compare.add_argument(
        "--baseline",
        default=None,
        help=f"baseline file to compare with (default: ROOT/{DEFAULT_BASELINE_NAME})",
    )
    baseline_compare.add_argument("--format", choices=("text", "json"), default="text")
    baseline_compare.add_argument(
        "--fail-on",
        choices=("info", "warning", "error"),
        default=None,
        help="lowest new-finding severity that fails (default: from config, else warning)",
    )
    _add_scan_parser(commands)
    _add_doctor_parser(commands)
    return parser


def _cmd_scenarios(args: argparse.Namespace) -> int:
    """Print the scenario catalog."""
    if args.format == "json":
        default = DEFAULT_SET_NOTE.split(":", 1)[1].strip().split(", ")
        payload = {
            "scenarios": [info.to_dict() for info in SCENARIO_CATALOG],
            "default": default,
        }
        emit(json.dumps(payload, indent=2) + "\n", sys.stdout)
        return ExitCode.OK
    lines = [f"{info.family:<10} {info.summary}" for info in SCENARIO_CATALOG]
    lines += ["", DEFAULT_SET_NOTE, "Run 'assumptionradar explain <name>' for details."]
    emit("".join(line + "\n" for line in lines), sys.stdout)
    return ExitCode.OK


def _cmd_explain(args: argparse.Namespace) -> int:
    """Describe one scenario family or full scenario name."""
    info = find_scenario_info(args.name)
    if info is None:
        families = ", ".join(entry.family for entry in SCENARIO_CATALOG)
        emit(
            f"assumptionradar: unknown scenario {args.name!r}; known: {families}\n",
            sys.stderr,
        )
        return ExitCode.USAGE
    if args.format == "json":
        emit(json.dumps(info.to_dict(), indent=2) + "\n", sys.stdout)
        return ExitCode.OK
    lines = [
        info.family,
        f"  use:     {info.syntax}",
        f"  changes: {info.summary}",
        f"  reveals: {info.reveals}",
        f"  fix:     {info.fix}",
    ]
    lines += [f"  note:    {note}" for note in info.notes]
    emit("".join(line + "\n" for line in lines), sys.stdout)
    return ExitCode.OK


def _shape_problem(data: dict[str, Any]) -> str | None:
    """Return a description of the first schema v1 shape problem, or None."""
    missing = [key for key in _REPORT_KEYS if key not in data]
    if missing:
        return "missing " + ", ".join(missing)
    if not isinstance(data["verifications"], list):
        return "'verifications' is not a list"
    if not isinstance(data["verification_skipped"], list):
        return "'verification_skipped' is not a list"
    observation = data["observation"]
    if observation is not None and not isinstance(observation, dict):
        return "'observation' is neither an object nor null"
    for item in data["verifications"]:
        if not isinstance(item, dict) or not isinstance(item.get("status"), str):
            return "a verification has no string 'status'"
    return None


class _ReportError(Exception):
    """A report file cannot be used; the message is a one-line user-facing reason."""


def _load_report(path: str) -> dict[str, Any]:
    """Read and validate a report file.

    Args:
        path: Path to the JSON report.

    Returns:
        The report document as a dict.

    Raises:
        _ReportError: If the file cannot be read, is not JSON, or is not a v1 report.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise _ReportError(f"cannot read report {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise _ReportError(f"{path} is not valid JSON: {exc}") from exc
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _ReportError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _ReportError(f"{path}: not an AssumptionRadar report (expected a JSON object)")
    version = data.get("schema_version")
    if version != _REPORT_SCHEMA:
        raise _ReportError(
            f"{path}: unsupported report schema_version {version!r}; "
            f"this version reads '{_REPORT_SCHEMA}'"
        )
    detail = _shape_problem(data)
    if detail is not None:
        raise _ReportError(f"{path}: report does not match schema v1: {detail}")
    return data


def _report_exit(data: dict[str, Any], exit_zero: bool) -> ExitCode:
    """Choose the exit code from the verification statuses of a report."""
    if exit_zero:
        return ExitCode.OK
    statuses = {str(item["status"]) for item in data["verifications"]}
    if "regression" in statuses:
        return ExitCode.FINDINGS
    if statuses & {"inconclusive", "error"}:
        return ExitCode.INCONCLUSIVE
    return ExitCode.OK


def _cmd_report_show(args: argparse.Namespace) -> int:
    """Render or print a saved report."""
    try:
        data = _load_report(args.path)
    except _ReportError as exc:
        emit(f"assumptionradar: {exc}\n", sys.stderr)
        return ExitCode.USAGE
    if args.format == "json":
        emit(json.dumps(data, indent=2) + "\n", sys.stdout)
        return _report_exit(data, args.exit_zero)
    try:
        lines = render_report(data)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        emit(
            f"assumptionradar: {args.path}: report does not match schema v1: {exc!r}\n",
            sys.stderr,
        )
        return ExitCode.USAGE
    if args.color == "always":
        color = True
    elif args.color == "never":
        color = False
    else:
        color = use_color(sys.stdout)
    emit(to_text(lines, color=color), sys.stdout)
    return _report_exit(data, args.exit_zero)


def _cmd_config_check(args: argparse.Namespace) -> int:
    """Validate the configuration and report the result."""
    root = Path(args.root)
    if not root.is_dir():
        emit(f"assumptionradar: {root} is not a directory\n", sys.stderr)
        return ExitCode.USAGE
    config_path = Path(args.config) if args.config is not None else None
    try:
        settings = load_settings(root, config_path)
    except ConfigurationError as exc:
        emit(f"assumptionradar: {exc}\n", sys.stderr)
        return ExitCode.USAGE
    if args.format == "json":
        payload = settings.to_dict()
        payload["active_rules"] = [
            rule.rule_id for rule in rules_from_settings(settings, load_project(root))
        ]
        emit(json.dumps(payload, indent=2) + "\n", sys.stdout)
        return ExitCode.OK
    if settings.config_file is None:
        emit("config OK: no config file found, using defaults\n", sys.stdout)
    else:
        emit(f"config OK: {settings.config_file}\n", sys.stdout)
    return ExitCode.OK


def _scan_for_baseline(root: Path, config: str | None) -> tuple[Settings, ScanResult] | None:
    """Load the settings and scan the project for a baseline command.

    Problems are reported on stderr here, so a ``None`` result means the caller
    should return ``ExitCode.USAGE``.

    Args:
        root: The project root directory.
        config: An explicit configuration file path, or None for discovery.

    Returns:
        The settings and the scan result, or None when the root or the settings are invalid.
    """
    if not root.is_dir():
        emit(f"assumptionradar: {root} is not a directory\n", sys.stderr)
        return None
    config_path = Path(config) if config is not None else None
    try:
        settings = load_settings(root, config_path)
    except ConfigurationError as exc:
        emit(f"assumptionradar: {exc}\n", sys.stderr)
        return None
    result = scan_with_settings(root, settings)
    for error in result.errors:
        emit(
            f"assumptionradar: warning: {error.relpath} could not be scanned "
            f"({error.kind}): {error.message}\n",
            sys.stderr,
        )
    return settings, result


def _cmd_baseline_create(args: argparse.Namespace) -> int:
    """Record the current findings in a baseline file."""
    root = Path(args.root)
    scanned = _scan_for_baseline(root, args.config)
    if scanned is None:
        return ExitCode.USAGE
    _, result = scanned
    output = Path(args.output) if args.output is not None else root / DEFAULT_BASELINE_NAME
    baseline = Baseline.from_findings(result.findings)
    try:
        write_baseline(output, baseline)
    except OSError as exc:
        emit(f"assumptionradar: cannot write {output}: {exc}\n", sys.stderr)
        return ExitCode.USAGE
    count = len(baseline.entries)
    noun = "finding" if count == 1 else "findings"
    emit(f"baseline written: {output} ({count} {noun})\n", sys.stdout)
    return ExitCode.OK


def _cmd_baseline_compare(args: argparse.Namespace) -> int:
    """Compare the current findings with a baseline and report the differences."""
    root = Path(args.root)
    scanned = _scan_for_baseline(root, args.config)
    if scanned is None:
        return ExitCode.USAGE
    settings, result = scanned
    if args.baseline is not None:
        baseline_path = Path(args.baseline)
    else:
        baseline_path = root / DEFAULT_BASELINE_NAME
    try:
        baseline = load_baseline(baseline_path)
    except BaselineError as exc:
        emit(f"assumptionradar: {exc}\n", sys.stderr)
        return ExitCode.USAGE
    settings = settings.with_overrides(fail_on=args.fail_on)
    comparison = compare(baseline, result.findings)
    failing = comparison.failing(Severity.parse(settings.fail_on))
    if args.format == "json":
        payload: dict[str, object] = {"baseline": str(baseline_path), **comparison.to_dict()}
        emit(json.dumps(payload, indent=2) + "\n", sys.stdout)
    else:
        lines = [
            f"baseline {baseline_path}: {len(comparison.new)} new, "
            f"{len(comparison.unchanged)} unchanged, {len(comparison.resolved)} resolved\n"
        ]
        for finding in comparison.new:
            loc = finding.location
            where = "-"
            if loc is not None:
                where = f"{loc.path}:{loc.line}" if loc.line is not None else loc.path
            lines.append(f"new       {finding.rule_id}  {where}  {finding.message}\n")
        for entry in comparison.resolved:
            where = entry.path if entry.path is not None else "-"
            lines.append(f"resolved  {entry.rule_id}  {where}  {entry.message}\n")
        emit("".join(lines), sys.stdout)
    return ExitCode.FINDINGS if failing else ExitCode.OK


def _scan_body(args: argparse.Namespace, settings: Settings, result: ScanResult, root: Path) -> str:
    """Build the text written by ``scan`` in the selected format.

    Args:
        args: The parsed ``scan`` arguments.
        settings: The effective settings, after command-line overrides.
        result: The completed scan.
        root: The project root that was scanned.

    Returns:
        The report text, ending with a newline.
    """
    if settings.format == "json":
        return json.dumps(build_scan_report(result, root=root, settings=settings), indent=2) + "\n"
    if settings.format == "sarif":
        return json.dumps(sarif_from_scan(result, root=root), indent=2) + "\n"
    if args.output is not None or args.color == "never":
        color = False
    elif args.color == "always":
        color = True
    else:
        color = use_color(sys.stdout)
    lines = render_scan(
        result, settings=settings, verbose=args.verbose, quiet=args.quiet, root=root
    )
    return to_text(lines, color=color)


def _cmd_scan(args: argparse.Namespace) -> int:
    """Scan a project and print or write the report."""
    root = Path(args.root)
    if not root.is_dir():
        emit(f"assumptionradar: {root} is not a directory\n", sys.stderr)
        return ExitCode.USAGE
    config_path = Path(args.config) if args.config is not None else None
    try:
        settings = load_settings(root, config_path).with_overrides(
            format=args.format,
            fail_on=args.fail_on,
            strict_suppressions=True if args.strict_suppressions else None,
            cache=False if args.no_cache else None,
        )
    except ConfigurationError as exc:
        emit(f"assumptionradar: {exc}\n", sys.stderr)
        return ExitCode.USAGE
    result = scan_with_settings(root, settings)
    body = _scan_body(args, settings, result, root)
    if args.output is None:
        emit(body, sys.stdout)
    else:
        target = Path(args.output)
        try:
            target.write_text(body, encoding="utf-8")
        except OSError as exc:
            emit(f"assumptionradar: cannot write {target}: {exc}\n", sys.stderr)
            return ExitCode.USAGE
        if not args.quiet:
            emit(f"report written: {target}\n", sys.stdout)
    if args.exit_zero:
        return ExitCode.OK
    return ExitCode.FINDINGS if is_failing(result, settings) else ExitCode.OK


def _pytest_version() -> str | None:
    """Return the installed pytest version, or None when pytest is not installed."""
    try:
        return importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        return None


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Print the environment, configuration and active rules for diagnosis."""
    root = Path(args.root)
    if not root.is_dir():
        emit(f"assumptionradar: {root} is not a directory\n", sys.stderr)
        return ExitCode.USAGE
    config_path = Path(args.config) if args.config is not None else None
    config_error: str | None = None
    try:
        settings = load_settings(root, config_path)
    except ConfigurationError as exc:
        settings = Settings()
        config_error = str(exc)
    project = load_project(root)
    active = [rule.rule_id for rule in rules_from_settings(settings, project)]
    pytest_version = _pytest_version()
    config_file = str(settings.config_file) if settings.config_file is not None else None
    cache_dir = str(root / settings.cache_dir)
    if args.format == "json":
        payload: dict[str, object] = {
            "assumptionradar": __version__,
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "executable": sys.executable,
            },
            "platform": sys.platform,
            "root": str(root),
            "config_file": config_file,
            "config_error": config_error,
            "pyproject": project.has_pyproject,
            "active_rules": active,
            "cache": {"enabled": settings.cache, "directory": cache_dir},
            "pytest": pytest_version,
        }
        emit(json.dumps(payload, indent=2) + "\n", sys.stdout)
    else:
        if config_error is not None:
            config_text = f"ERROR: {config_error}"
        elif config_file is None:
            config_text = "none found, using defaults"
        else:
            config_text = config_file
        rows = [
            ("assumptionradar", __version__),
            (
                "python",
                f"{platform.python_version()} ({platform.python_implementation()}) "
                f"{sys.executable}",
            ),
            ("platform", sys.platform),
            ("project root", str(root)),
            ("config", config_text),
            (
                "pyproject.toml",
                "found" if project.has_pyproject else "not found (AR-IMP-001 needs one)",
            ),
            ("active rules", ", ".join(active)),
            ("cache", cache_dir if settings.cache else "disabled"),
            ("pytest", pytest_version if pytest_version is not None else "not installed"),
        ]
        emit("".join(f"{label:<17}{value}\n" for label, value in rows), sys.stdout)
    return ExitCode.USAGE if config_error is not None else ExitCode.OK


def _exit_status(code: str | int | None) -> int:
    """Convert the code of an argparse ``SystemExit`` into an exit status."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    emit(f"{code}\n", sys.stderr)
    return ExitCode.USAGE


def _dispatch(args: argparse.Namespace) -> int:
    """Run the subcommand selected by the parsed arguments."""
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "scenarios": _cmd_scenarios,
        "explain": _cmd_explain,
        "report": _cmd_report_show,
        "config": _cmd_config_check,
        "scan": _cmd_scan,
        "doctor": _cmd_doctor,
    }
    handler: Callable[[argparse.Namespace], int] | None
    if args.command == "baseline":
        handler = (
            _cmd_baseline_create if args.baseline_command == "create" else _cmd_baseline_compare
        )
    else:
        handler = handlers.get(args.command)
    if handler is None:
        return ExitCode.USAGE
    return handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AssumptionRadar command-line interface.

    Parse the given arguments (or ``sys.argv`` when ``argv`` is ``None``) and
    handle them. With no arguments, the help text is printed to stdout.

    Args:
        argv: Command-line arguments excluding the program name, or ``None`` to
            use ``sys.argv[1:]``.

    Returns:
        The process exit code, one of :class:`ExitCode`.

    """
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        emit(parser.format_help(), sys.stdout)
        return 0
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return _exit_status(exc.code)
    try:
        return _dispatch(args)
    except Exception as exc:  # last-resort guard so users never see a traceback
        emit(f"assumptionradar: internal error: {type(exc).__name__}: {exc}\n", sys.stderr)
        return ExitCode.INTERNAL
