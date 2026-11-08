"""Execute every code block in the user documentation (P1-17).

Each fenced block in README.md and docs/*.md must be preceded by a marker:

- ``<!-- doctest: file PATH -->``   write the block to PATH in the sandbox
- ``<!-- doctest: run [exit=N] -->`` run the one command in the block (default exit 0)
- ``<!-- doctest: output -->``       the previous command's output must contain these
  lines in order (substring match); a ``...`` line skips any number of lines
- ``<!-- doctest: skip REASON -->``  not executed, with a reason

A block without a marker fails the test, so no example can silently rot. Each
document runs in its own temporary directory.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
MARKER_RE = re.compile(r"^<!-- doctest: (?P<action>file|run|output|skip)(?P<arg>.*?) -->$")
FENCE_RE = re.compile(r"^```(?P<lang>[\w+-]*)\s*$")
LINK_RE = re.compile(r"\]\((?P<target>[^)\s]+)\)")
COMMANDS = {"pytest": "pytest", "assumptionradar": "assumptionradar"}


@dataclass(frozen=True)
class Block:
    line: int
    action: str
    arg: str
    lang: str
    body: str


def parse(text: str, name: str) -> list[Block]:
    """Split a Markdown document into marked code blocks; fail on unmarked ones."""
    lines = text.splitlines()
    blocks: list[Block] = []
    pending: tuple[str, str] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = MARKER_RE.match(line.strip())
        fence = FENCE_RE.match(line)
        if marker:
            pending = (marker["action"], marker["arg"].strip())
        elif fence:
            start = i + 1
            end = start
            while end < len(lines) and lines[end].strip() != "```":
                end += 1
            assert end < len(lines), f"{name}:{i + 1}: unclosed code block"
            assert pending is not None, f"{name}:{i + 1}: code block without a doctest marker"
            action, arg = pending
            blocks.append(Block(i + 1, action, arg, fence["lang"], "\n".join(lines[start:end])))
            pending = None
            i = end
        elif line.strip() and pending is not None:
            raise AssertionError(f"{name}:{i + 1}: doctest marker not followed by a code block")
        i += 1
    assert pending is None, f"{name}: doctest marker at end of file"
    return blocks


def run_command(command: str, cwd: Path) -> tuple[int, str]:
    argv = shlex.split(command)
    assert argv[0] in COMMANDS, f"unsupported command in docs: {argv[0]}"
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [sys.executable, "-m", COMMANDS[argv[0]], *argv[1:]],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def check_output(expected: str, actual: str, where: str) -> None:
    lines = [line.rstrip() for line in actual.splitlines()]
    pos = 0
    skipping = False
    for want in expected.splitlines():
        if want.strip() == "...":
            skipping = True
            continue
        found = next((k for k in range(pos, len(lines)) if want.rstrip() in lines[k]), None)
        assert found is not None, (
            f"{where}: expected line not found: {want!r}\n--- actual ---\n{actual}"
        )
        assert skipping or pos in (found, 0), (
            f"{where}: unexpected lines before {want!r}\n--- actual ---\n{actual}"
        )
        pos = found + 1
        skipping = False


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_doc_examples_run(doc: Path, tmp_path: Path) -> None:
    name = doc.relative_to(ROOT).as_posix()
    last: str | None = None
    for block in parse(doc.read_text(encoding="utf-8"), name):
        where = f"{name}:{block.line}"
        if block.action == "skip":
            assert block.arg, f"{where}: skip needs a reason"
        elif block.action == "file":
            target = tmp_path / block.arg
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block.body + "\n", encoding="utf-8")
        elif block.action == "run":
            commands = [c for c in block.body.splitlines() if c.strip()]
            assert len(commands) == 1, f"{where}: a run block holds exactly one command"
            expected_exit = 0
            if block.arg:
                key, _, value = block.arg.partition("=")
                assert key == "exit", f"{where}: unknown run option {block.arg!r}"
                expected_exit = int(value)
            code, last = run_command(commands[0], tmp_path)
            assert code == expected_exit, f"{where}: exit {code}, expected {expected_exit}\n{last}"
        else:
            assert last is not None, f"{where}: output block before any run block"
            check_output(block.body, last, where)


def test_user_docs_exist() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in DOCS}
    assert {"README.md", "docs/quickstart.md", "docs/limitations.md", "docs/security.md"} <= names


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_exist(doc: Path) -> None:
    missing = []
    for match in LINK_RE.finditer(doc.read_text(encoding="utf-8")):
        target = match["target"].split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        if not (doc.parent / target).exists():
            missing.append(target)
    assert missing == []


# ---------------------------------------------------------------- the parser


def test_unmarked_block_is_rejected() -> None:
    with pytest.raises(AssertionError, match="without a doctest marker"):
        parse("text\n\n```bash\npytest\n```\n", "x.md")


def test_marker_must_precede_block() -> None:
    with pytest.raises(AssertionError, match="not followed by a code block"):
        parse("<!-- doctest: run -->\nsome prose\n```bash\npytest\n```\n", "x.md")


def test_parse_blocks() -> None:
    text = "<!-- doctest: file a/b.py -->\n```python\nx = 1\n```\n"
    assert parse(text, "x.md") == [Block(2, "file", "a/b.py", "python", "x = 1")]


def test_output_matching() -> None:
    check_output("a\n...\nc", "a\nb\nc\n", "x")
    check_output("b", "a\nb\n", "x")  # leading lines are allowed
    with pytest.raises(AssertionError, match="unexpected lines"):
        check_output("a\nc", "a\nb\nc\n", "x")
    with pytest.raises(AssertionError, match="not found"):
        check_output("z", "a\n", "x")
