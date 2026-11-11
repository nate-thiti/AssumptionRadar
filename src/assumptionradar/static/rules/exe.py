"""AR-EXE-001: a command is run by bare name, so PATH decides which program runs.

The rule parses the source only; the scanned code is never executed. A call is
reported when a known process-spawning sink receives a bare program name (no
path separator, no drive letter, no variable) and no ``shutil.which`` check for
that name is made in the call's own scope or an enclosing scope.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Final

from assumptionradar.core.fingerprint import compute_fingerprint
from assumptionradar.core.models import (
    Category,
    Confidence,
    Evidence,
    EvidenceKind,
    Finding,
    Severity,
    SourceLocation,
)
from assumptionradar.static.imports import ImportTable
from assumptionradar.static.parsing import ParsedModule

RULE_ID: Final = "AR-EXE-001"
RULE_VERSION: Final = "1"
TITLE: Final = "Command is looked up on PATH"
PYTHON_REMEDIATION: Final = (
    "Run Python tools with sys.executable (for example [sys.executable, '-m', 'pip']) "
    "so the same interpreter is used everywhere."
)
GENERIC_REMEDIATION: Final = (
    "Check for the tool with shutil.which() and skip or fail with a clear message, "
    "or make the executable path configurable."
)
FAILURE: Final = (
    "A machine or CI image without the tool, or with a different version first on PATH, "
    "fails with FileNotFoundError or behaves differently."
)

_WHICH: Final = "shutil.which"
_ASSIGNMENT_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_DRIVE_RE: Final = re.compile(r"^[A-Za-z]:")
_PATH_CHARS: Final[frozenset[str]] = frozenset("/\\=")
_NON_BARE_START: Final[frozenset[str]] = frozenset("$%~")

SHELL_BUILTINS: Final[frozenset[str]] = frozenset(
    {
        ".",
        ":",
        "[",
        "alias",
        "break",
        "call",
        "cd",
        "cls",
        "command",
        "continue",
        "copy",
        "del",
        "dir",
        "echo",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "for",
        "if",
        "kill",
        "md",
        "mkdir",
        "move",
        "printf",
        "pwd",
        "rd",
        "read",
        "ren",
        "rmdir",
        "set",
        "shift",
        "source",
        "start",
        "test",
        "trap",
        "true",
        "type",
        "ulimit",
        "umask",
        "unset",
        "ver",
        "wait",
    }
)

_AUTO: Final = "auto"
_SHELL: Final = "shell"
_EXEC: Final = "exec"


@dataclass(frozen=True, slots=True)
class _SinkSpec:
    """How a sink takes its command argument.

    Attributes:
        position: Index of the positional command argument.
        keyword: Name of the keyword that may carry the command argument.
        kind: ``"auto"`` (shell only when ``shell=True``), ``"shell"`` (always a
            shell string) or ``"exec"`` (never a shell string).
    """

    position: int
    keyword: str
    kind: str


_SINKS: Final[dict[str, _SinkSpec]] = {
    "subprocess.run": _SinkSpec(0, "args", _AUTO),
    "subprocess.call": _SinkSpec(0, "args", _AUTO),
    "subprocess.check_call": _SinkSpec(0, "args", _AUTO),
    "subprocess.check_output": _SinkSpec(0, "args", _AUTO),
    "subprocess.Popen": _SinkSpec(0, "args", _AUTO),
    "subprocess.getoutput": _SinkSpec(0, "cmd", _SHELL),
    "subprocess.getstatusoutput": _SinkSpec(0, "cmd", _SHELL),
    "os.system": _SinkSpec(0, "command", _SHELL),
    "os.popen": _SinkSpec(0, "cmd", _SHELL),
    "os.execlp": _SinkSpec(0, "file", _EXEC),
    "os.execlpe": _SinkSpec(0, "file", _EXEC),
    "os.execvp": _SinkSpec(0, "file", _EXEC),
    "os.execvpe": _SinkSpec(0, "file", _EXEC),
    "os.spawnlp": _SinkSpec(1, "file", _EXEC),
    "os.spawnlpe": _SinkSpec(1, "file", _EXEC),
    "os.spawnvp": _SinkSpec(1, "file", _EXEC),
    "os.spawnvpe": _SinkSpec(1, "file", _EXEC),
    "asyncio.create_subprocess_exec": _SinkSpec(0, "program", _EXEC),
    "asyncio.create_subprocess_shell": _SinkSpec(0, "cmd", _SHELL),
}


def is_bare_program(name: str) -> bool:
    """Return True when ``name`` is a program name that PATH lookup would resolve.

    Args:
        name: The first word of a command, as written in the source.

    Returns:
        False for empty names, names with whitespace, path separators or ``=``,
        drive-letter names, and names starting with ``$``, ``%`` or ``~``.
    """
    if not name:
        return False
    if any(ch.isspace() for ch in name):
        return False
    if any(ch in _PATH_CHARS for ch in name):
        return False
    if _DRIVE_RE.match(name):
        return False
    return name[0] not in _NON_BARE_START


def program_from_command(text: str, *, shell: bool) -> str | None:
    """Return the program a command string runs by name, if it is a bare name.

    Args:
        text: The command string.
        shell: True when the string is interpreted by a shell, so leading
            ``NAME=value`` assignments and shell builtins are skipped or ignored.

    Returns:
        The first bare program name, or None when there is none.
    """
    tokens = text.split()
    if shell:
        while tokens and _ASSIGNMENT_RE.match(tokens[0]):
            tokens.pop(0)
    if not tokens:
        return None
    first = tokens[0]
    if shell and first in SHELL_BUILTINS:
        return None
    return first if is_bare_program(first) else None


def _effective_shell(node: ast.Call, kind: str) -> bool:
    """Return whether a sink call runs its command through a shell.

    Args:
        node: The call expression.
        kind: The sink kind from its specification.

    Returns:
        True for shell sinks, False for exec sinks, and for auto sinks only when
        a ``shell`` keyword is the constant True.
    """
    if kind == _SHELL:
        return True
    if kind == _EXEC:
        return False
    return any(
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


def _command_arg(node: ast.Call, spec: _SinkSpec) -> ast.expr | None:
    """Return the expression holding the command of a sink call.

    Args:
        node: The call expression.
        spec: The sink specification.

    Returns:
        The positional argument at ``spec.position`` or the keyword ``spec.keyword``,
        or None when it cannot be determined statically.
    """
    args = node.args
    has_starred = any(isinstance(arg, ast.Starred) for arg in args)
    if spec.position < len(args) and not any(
        isinstance(arg, ast.Starred) for arg in args[: spec.position + 1]
    ):
        return args[spec.position]
    if has_starred:
        return None
    for keyword in node.keywords:
        if keyword.arg == spec.keyword:
            return keyword.value
    return None


def _program(node: ast.expr, shell: bool) -> tuple[str, bool] | None:
    """Return the bare program a command expression runs, and whether it was a shell string.

    Args:
        node: The command expression.
        shell: Whether the sink runs its command through a shell.

    Returns:
        ``(program, from_shell_string)``, or None when the program is not a bare name
        known statically.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        if not node.elts or isinstance(node.elts[0], ast.Starred):
            return None
        first = node.elts[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return None
        program = program_from_command(first.value, shell=False)
        return None if program is None else (program, False)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _program(node.left, shell)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        program = program_from_command(node.value, shell=shell)
        return None if program is None else (program, shell)
    return None


@dataclass(frozen=True, slots=True)
class _Call:
    """A sink call that runs a bare program name.

    Attributes:
        node: The call expression.
        sink: The qualified name of the sink, such as ``subprocess.run``.
        program: The bare program name.
        shell: Effective shell flag of the sink call.
        from_shell_string: True when the program was read from a shell command string.
        symbol: Dotted name of the enclosing function or class, if any.
        chain: Identifiers of the enclosing scopes, innermost last.
    """

    node: ast.Call
    sink: str
    program: str
    shell: bool
    from_shell_string: bool
    symbol: str | None
    chain: tuple[int, ...]


class _ExecVisitor(ast.NodeVisitor):
    """Collect sink calls and ``shutil.which`` guards, tracking scopes."""

    def __init__(self, imports: ImportTable) -> None:
        """Create a visitor.

        Args:
            imports: The import table of the module being visited.
        """
        self._imports = imports
        self._names: list[str] = []
        self._frames: list[int] = []
        self._frame_count = 0
        self.calls: list[_Call] = []
        self.checks: dict[int, set[str]] = {}

    def _push_frame(self) -> None:
        """Push a new scope frame."""
        self._frame_count += 1
        self._frames.append(self._frame_count)

    def _enter_scope(self, node: ast.AST, name: str) -> None:
        """Visit the children of a def or class node with ``name`` pushed on the scope stack."""
        self._names.append(name)
        self._push_frame()
        self.generic_visit(node)
        self._frames.pop()
        self._names.pop()

    def _symbol(self) -> str | None:
        """Return the dotted name of the enclosing def or class, if any."""
        return ".".join(self._names) or None

    def _add_guard(self, node: ast.Call) -> None:
        """Record a ``shutil.which`` check of a literal name in the innermost scope.

        Args:
            node: The ``shutil.which`` call.
        """
        candidate: ast.expr | None = node.args[0] if node.args else None
        if candidate is None:
            for keyword in node.keywords:
                if keyword.arg == "cmd":
                    candidate = keyword.value
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            self.checks.setdefault(self._frames[-1], set()).add(candidate.value)

    def _add_sink(self, node: ast.Call, sink: str, spec: _SinkSpec) -> None:
        """Record a sink call whose command is a bare program name.

        Args:
            node: The sink call.
            sink: The qualified name of the sink.
            spec: The sink specification.
        """
        if any(keyword.arg == "executable" for keyword in node.keywords):
            return
        command = _command_arg(node, spec)
        if command is None:
            return
        shell = _effective_shell(node, spec.kind)
        resolved = _program(command, shell)
        if resolved is None:
            return
        program, from_shell_string = resolved
        self.calls.append(
            _Call(
                node,
                sink,
                program,
                shell,
                from_shell_string,
                self._symbol(),
                tuple(self._frames),
            )
        )

    def visit_Module(self, node: ast.Module) -> None:
        """Visit the module scope."""
        self._push_frame()
        self.generic_visit(node)
        self._frames.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit a function definition."""
        self._enter_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit an async function definition."""
        self._enter_scope(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit a class definition."""
        self._enter_scope(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        """Record ``shutil.which`` guards and process-spawning sink calls."""
        qualified = self._imports.resolve(node.func)
        if qualified == _WHICH:
            self._add_guard(node)
        spec = _SINKS.get(qualified) if qualified is not None else None
        if qualified is not None and spec is not None:
            self._add_sink(node, qualified, spec)
        self.generic_visit(node)


def _scan(module: ParsedModule) -> _ExecVisitor:
    """Run the visitor over one parsed module.

    Args:
        module: The parsed module to inspect.

    Returns:
        The visitor holding the sink calls and guards of the module.
    """
    visitor = _ExecVisitor(module.imports)
    visitor.visit(module.tree)
    return visitor


def _is_guarded(call: _Call, checks: dict[int, set[str]]) -> bool:
    """Return True when the program of a sink call is checked in its scope chain.

    Args:
        call: The sink call.
        checks: Checked program names per scope identifier.

    Returns:
        True when a ``shutil.which`` check of the program exists in the call's own
        scope or an enclosing scope.
    """
    return any(ident in checks and call.program in checks[ident] for ident in call.chain)


def _build(module: ParsedModule, call: _Call) -> Finding:
    """Build the AR-EXE-001 finding for one unguarded sink call.

    Args:
        module: The parsed module containing the call.
        call: The sink call.

    Returns:
        The AR-EXE-001 finding.
    """
    program = call.program
    location = SourceLocation(
        module.relpath, call.node.lineno, call.node.col_offset + 1, call.symbol
    )
    if program == "py" or program.startswith(("python", "pip")):
        remediation = PYTHON_REMEDIATION
    else:
        remediation = GENERIC_REMEDIATION
    return Finding(
        rule_id=RULE_ID,
        title=TITLE,
        message=(
            f"{call.sink}() runs {program!r} by name, so the result depends on the PATH "
            "of the machine that runs it"
        ),
        category=Category.EXECUTABLES,
        severity=Severity.WARNING,
        confidence=Confidence.MEDIUM if call.from_shell_string else Confidence.HIGH,
        fingerprint=compute_fingerprint(
            RULE_ID, module.relpath, call.symbol, f"{call.sink} {program}"
        ),
        location=location,
        evidence=(
            Evidence(
                EvidenceKind.INFERRED,
                "ast",
                f"{call.sink}() receives the bare program name {program!r}",
                location,
            ),
        ),
        remediation=remediation,
        failure_scenario=FAILURE,
        metadata={
            "sink": call.sink,
            "program": program,
            "shell": "true" if call.shell else "false",
        },
    )


class PathLookupRule:
    """Report process-spawning calls that run a bare program name found through PATH.

    Attributes:
        rule_id: The rule identifier, ``AR-EXE-001``.
        version: The rule version, bumped when findings change.
    """

    rule_id: str = RULE_ID
    version: str = RULE_VERSION

    def check(self, module: ParsedModule) -> list[Finding]:
        """Return one finding per unguarded bare-name sink call.

        Args:
            module: The parsed module to inspect.

        Returns:
            The findings for ``module``, in source order.
        """
        visitor = _scan(module)
        return [
            _build(module, call) for call in visitor.calls if not _is_guarded(call, visitor.checks)
        ]
