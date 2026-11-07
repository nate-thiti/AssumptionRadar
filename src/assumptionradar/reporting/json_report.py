"""JSON report for the pytest plugin.

The report is a single document with a fixed top-level key order. Writes are
atomic: the document is written to a temporary file beside the target and then
moved into place with ``os.replace``.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import sys
import tempfile
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path

import assumptionradar
from assumptionradar.analysis.runtime import Observation
from assumptionradar.analysis.verification import VerificationResult

SCHEMA_VERSION = "1"
SCHEMA_RESOURCE = f"schemas/report-v{SCHEMA_VERSION}.json"


def load_report_schema() -> dict[str, object]:
    """Return the JSON Schema (draft 2020-12) for reports of the current version.

    The schema ships inside the package, so it matches the installed version.
    """
    text = (
        resources.files("assumptionradar.reporting")
        .joinpath(SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    schema: dict[str, object] = json.loads(text)
    return schema


def build_pytest_report(
    observation: Observation | None,
    verifications: Sequence[VerificationResult],
    skipped: Sequence[tuple[str, str]],
) -> dict[str, object]:
    """Assemble the JSON-compatible report for one pytest session.

    Args:
        observation: Runtime observation of the session, or None when observation was off.
        verifications: Verification results, one per verified test.
        skipped: Pairs of ``(test_id, reason)`` for tests that were not verified.

    Returns:
        A dict whose keys appear in a fixed order.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "assumptionradar", "version": assumptionradar.__version__},
        "platform": {"system": sys.platform, "python": platform.python_version()},
        "observation": observation.to_dict() if observation is not None else None,
        "verifications": [result.to_dict() for result in verifications],
        "verification_skipped": [{"test_id": t, "reason": r} for t, r in skipped],
    }


def write_json(path: Path, data: Mapping[str, object]) -> None:
    """Write ``data`` to ``path`` atomically as UTF-8 JSON.

    Args:
        path: Destination file. Its parent directory must exist.
        data: JSON-compatible mapping to serialise.

    Raises:
        OSError: If the temporary file cannot be created, written, or moved into place.
    """
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed explicitly below
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
        prefix=f".{path.name}.",
    )
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(temp, path)  # noqa: PTH105 - spec requires os.replace for atomic swap
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
