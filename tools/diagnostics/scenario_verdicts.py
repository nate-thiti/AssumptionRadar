"""Print the real verdicts of the encoding and locale scenarios on this platform.

Run in CI on every OS to show whether each scenario takes effect there. The
integration tests accept ``unsupported`` where a platform cannot apply a
condition; this script shows which verdict actually occurred.
"""

from __future__ import annotations

import locale
import sys
import tempfile
import textwrap
from pathlib import Path

from assumptionradar.analysis.runner import SubprocessRunner
from assumptionradar.analysis.scenarios import encoding_scenario, locale_scenario
from assumptionradar.analysis.verification import Scenario, Verifier

NODE = "tests/test_f.py::test_it"


def project(body: str, files: dict[str, bytes] | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_f.py").write_text(textwrap.dedent(body), encoding="utf-8")
    for name, data in (files or {}).items():
        (root / name).write_bytes(data)
    return root


def show(root: Path, scenario: Scenario) -> None:
    result = Verifier(root, runner=SubprocessRunner()).verify(NODE, [scenario])
    verdict = result.scenarios[0]
    print(f"{scenario.name:16} -> {verdict.status.value:14} | {verdict.reason}")


def main() -> None:
    host = locale.getpreferredencoding(False)
    print(f"{sys.platform} Python {sys.version.split()[0]} preferred encoding: {host}")

    # A non-ASCII character the host encoding can represent: reading it back with
    # the default encoding passes in the baseline and should break when it changes.
    candidates = ("é", "ก", "ß", "Ж")
    char = next(c for c in candidates if c.encode(host, "ignore"))
    body = (
        "from pathlib import Path\n"
        "def test_it():\n"
        "    p = Path(__file__).parent.parent / 't.txt'\n"
        f"    assert open(p).read() == {char!r}\n"
    )
    show(project(body, {"t.txt": char.encode(host)}), encoding_scenario())

    locale_body = (
        "import locale\ndef test_it():\n    assert locale.format_string('%.1f', 1.5) == '1.5'\n"
    )
    for name in ("de_DE", "fr_FR", "tr_TR"):
        show(project(locale_body), locale_scenario(name))


if __name__ == "__main__":
    main()
