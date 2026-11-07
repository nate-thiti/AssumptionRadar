"""Catalog of verification scenario families, for ``scenarios`` and ``explain``.

One entry per scenario family (the part of a scenario name before the first
colon). This is the single source of the user-facing explanation and fix hint
for each family; the terminal renderer takes its ``fix:`` lines from here.
Static-analysis rules (``AR-...``) will be added to the catalog in v0.2.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScenarioInfo:
    """User-facing description of one scenario family.

    Attributes:
        family: Family name, e.g. ``tz``; scenario names start with it.
        syntax: How to request it in ``--assumption-verify``.
        summary: What the scenario changes, in one sentence.
        reveals: The hidden assumption a regression under it points to.
        fix: The usual way to remove that assumption.
        notes: Platform behaviour and limits worth knowing.
    """

    family: str
    syntax: str
    summary: str
    reveals: str
    fix: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "family": self.family,
            "syntax": self.syntax,
            "summary": self.summary,
            "reveals": self.reveals,
            "fix": self.fix,
            "notes": list(self.notes),
        }


SCENARIO_CATALOG: tuple[ScenarioInfo, ...] = (
    ScenarioInfo(
        family="cwd",
        syntax="cwd",
        summary="Run the test from an empty temporary working directory.",
        reveals=(
            "Code that opens files by paths relative to the current working directory, "
            "so it only works when pytest is started from one particular folder."
        ),
        fix="build paths from Path(__file__).parent or a fixture instead of the working directory",
    ),
    ScenarioInfo(
        family="tmpdir",
        syntax="tmpdir",
        summary=(
            "Point the temporary directory at a fresh folder whose path contains "
            "spaces and non-ASCII characters."
        ),
        reveals=(
            "Code that builds commands or file names from the temporary directory "
            "without quoting, or assumes an ASCII path."
        ),
        fix="use the tmp_path fixture instead of the system temporary directory",
    ),
    ScenarioInfo(
        family="tz",
        syntax="tz | tz:<zone>   (zones: +9, -5, utc)",
        summary="Run with a different local time zone, set through a POSIX TZ string.",
        reveals=(
            "Code that uses local time (datetime.now(), time.localtime(), naive "
            "datetimes) where it means a fixed time zone."
        ),
        fix=(
            "pass an explicit tzinfo (e.g. datetime.now(timezone.utc)) instead of relying "
            "on local time"
        ),
        notes=(
            "Plain 'tz' picks up to two zones whose offset differs from this machine's.",
            "Only POSIX TZ strings such as JST-9 are used: Windows silently ignores IANA names.",
        ),
    ),
    ScenarioInfo(
        family="encoding",
        syntax="encoding",
        summary=(
            "Flip the default text encoding: UTF-8 on machines with a legacy code page, "
            "a legacy encoding on UTF-8 machines."
        ),
        reveals=(
            "open(), read_text() or write_text() calls without encoding=, whose result "
            "depends on the machine's locale."
        ),
        fix="pass encoding= explicitly to open(), read_text() and write_text()",
        notes=(
            "On UTF-8 machines this sets PYTHONUTF8=0, PYTHONCOERCECLOCALE=0 and LC_ALL=C "
            "together; LC_ALL=C alone is undone by locale coercion.",
        ),
    ),
    ScenarioInfo(
        family="locale",
        syntax="locale:<name>   (names: de_DE, fr_FR, tr_TR)",
        summary="Run with a different locale, applied inside the child with locale.setlocale().",
        reveals=(
            "Number, date or text handling that depends on the locale, such as "
            "locale.format_string(), locale.atof() or time.strftime('%A')."
        ),
        fix="avoid locale-dependent formatting, or set the locale explicitly in a fixture",
        notes=(
            "Reported as unsupported when the locale is not installed; minimal Linux "
            "images often have only C and C.UTF-8.",
        ),
    ),
    ScenarioInfo(
        family="env-unset",
        syntax="env-unset:<NAME>",
        summary="Remove one environment variable for the child process.",
        reveals=(
            "Code that requires the variable (os.environ['NAME']) without a default or "
            "a clear error message."
        ),
        fix="give the variable a default, or set it in a fixture with monkeypatch.setenv",
        notes=(
            "Reported as unsupported when the variable is not set here to begin with.",
            "Only the variable's name is recorded, never its value.",
        ),
    ),
)

DEFAULT_SET_NOTE = "'default' expands to: cwd, tz, encoding, tmpdir"


def find_scenario_info(name: str) -> ScenarioInfo | None:
    """Return the catalog entry for a scenario name or family, or None.

    Args:
        name: A family (``tz``) or a full scenario name (``tz:+9``).
    """
    family = name.split(":", 1)[0]
    for info in SCENARIO_CATALOG:
        if info.family == family:
            return info
    return None
