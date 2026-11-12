"""Command-line interface for AssumptionRadar."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from enum import IntEnum
from pathlib import Path
from typing import Any, TextIO

from assumptionradar import __version__
from assumptionradar.config import load_settings, rules_from_settings
from assumptionradar.core.catalog import (
    DEFAULT_SET_NOTE,
    SCENARIO_CATALOG,
    find_scenario_info,
)
from assumptionradar.core.exceptions import ConfigurationError
from assumptionradar.reporting.terminal import render_report, to_text, use_color
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
        FINDINGS: At least one verified test has a regression.
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
    if args.command == "scenarios":
        return _cmd_scenarios(args)
    if args.command == "explain":
        return _cmd_explain(args)
    if args.command == "report":
        return _cmd_report_show(args)
    if args.command == "config":
        return _cmd_config_check(args)
    return ExitCode.USAGE


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
