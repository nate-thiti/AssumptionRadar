"""Command-line interface for AssumptionRadar."""

import argparse
import sys
from collections.abc import Sequence

from assumptionradar import __version__

_DESCRIPTION = "Discover hidden environmental assumptions before they break your software."


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AssumptionRadar command-line interface.

    Parse the given arguments (or ``sys.argv`` when ``argv`` is ``None``) and
    handle them. With no arguments, the help text is printed to stdout.

    Args:
        argv: Command-line arguments excluding the program name, or ``None`` to
            use ``sys.argv[1:]``.

    Returns:
        The process exit code. ``0`` on success.

    """
    parser = argparse.ArgumentParser(prog="assumptionradar", description=_DESCRIPTION)
    parser.add_argument(
        "--version",
        action="version",
        version=f"assumptionradar {__version__}",
    )
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0
    parser.parse_args(list(argv))
    return 0
