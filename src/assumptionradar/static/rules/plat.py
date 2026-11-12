"""AR-PLAT-001: platform-specific code without a platform check.

The rule parses the source only; the scanned code is never executed. It reports
path literals that only point to the intended file on one operating system,
calls to APIs and imports of modules that exist on only one platform. Code in a
scope that checks the platform (``sys.platform``, ``os.name``,
``platform.system()`` or ``hasattr``), and code guarded by ``TYPE_CHECKING`` or
by a ``try`` block that catches import or attribute errors, is not reported.
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
from assumptionradar.static.rules.cwd import (
    FILE_SINKS,
    PATH_CTORS,
    PATH_METHODS,
    select_argument,
)

RULE_ID: Final = "AR-PLAT-001"
RULE_VERSION: Final = "1"
TITLE: Final = "Platform-specific code without a platform check"
FAILURE_SCENARIO: Final = (
    "The code fails or touches the wrong file when it runs on another operating system, "
    "for example Windows CI or a macOS laptop."
)

POSIX_ONLY_APIS: Final[frozenset[str]] = frozenset(
    {
        "os.fork",
        "os.forkpty",
        "os.getuid",
        "os.geteuid",
        "os.getgid",
        "os.getegid",
        "os.getgroups",
        "os.setuid",
        "os.setgid",
        "os.seteuid",
        "os.setegid",
        "os.setsid",
        "os.setpgrp",
        "os.getpgid",
        "os.killpg",
        "os.chown",
        "os.lchown",
        "os.fchown",
        "os.getloadavg",
        "os.mkfifo",
        "os.mknod",
        "os.uname",
        "os.sync",
        "os.statvfs",
        "os.nice",
        "os.wait3",
        "os.wait4",
        "os.sched_getaffinity",
        "os.sched_setaffinity",
        "os.pathconf",
        "os.sysconf",
        "signal.SIGKILL",
        "signal.SIGHUP",
        "signal.SIGUSR1",
        "signal.SIGUSR2",
        "signal.SIGALRM",
        "signal.SIGCHLD",
        "signal.SIGQUIT",
        "signal.SIGPIPE",
        "signal.SIGSTOP",
        "signal.SIGCONT",
        "signal.SIGTSTP",
        "signal.alarm",
        "signal.setitimer",
        "signal.getitimer",
        "signal.pause",
        "signal.siginterrupt",
        "signal.pthread_kill",
        "signal.pthread_sigmask",
        "signal.sigwait",
        "socket.AF_UNIX",
    }
)

WINDOWS_ONLY_APIS: Final[frozenset[str]] = frozenset(
    {
        "os.startfile",
        "os.add_dll_directory",
        "os.O_BINARY",
        "signal.CTRL_C_EVENT",
        "signal.CTRL_BREAK_EVENT",
        "signal.SIGBREAK",
        "subprocess.STARTUPINFO",
        "subprocess.CREATE_NEW_CONSOLE",
        "subprocess.CREATE_NO_WINDOW",
        "subprocess.CREATE_NEW_PROCESS_GROUP",
        "subprocess.DETACHED_PROCESS",
        "subprocess.STARTF_USESHOWWINDOW",
        "subprocess.SW_HIDE",
        "ctypes.windll",
        "ctypes.WinDLL",
        "ctypes.oledll",
        "ctypes.WinError",
        "ctypes.FormatError",
        "ctypes.GetLastError",
    }
)

POSIX_ONLY_MODULES: Final[frozenset[str]] = frozenset(
    {
        "fcntl",
        "termios",
        "tty",
        "pty",
        "pwd",
        "grp",
        "resource",
        "posix",
        "syslog",
        "crypt",
        "spwd",
        "nis",
        "readline",
        "curses",
    }
)

WINDOWS_ONLY_MODULES: Final[frozenset[str]] = frozenset(
    {
        "msvcrt",
        "winreg",
        "winsound",
        "_winapi",
        "nt",
        "win32api",
        "win32con",
        "win32com",
        "pywintypes",
    }
)

_PLATFORM_REFS: Final[frozenset[str]] = frozenset(
    {
        "sys.platform",
        "os.name",
        "platform.system",
        "platform.platform",
        "platform.uname",
        "platform.win32_ver",
        "platform.mac_ver",
        "platform.libc_ver",
        "sys.getwindowsversion",
    }
)
_HASATTR_MODULES: Final[frozenset[str]] = frozenset(
    {"os", "signal", "socket", "subprocess", "ctypes", "sys", "time"}
)
_GUARD_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "builtins.ImportError",
        "builtins.ModuleNotFoundError",
        "builtins.AttributeError",
        "builtins.OSError",
        "builtins.Exception",
        "builtins.BaseException",
    }
)
_PATH_BUILDERS: Final[frozenset[str]] = frozenset(
    {"pathlib.Path", "pathlib.PurePath", "os.path.join"}
)
_DRIVE_RE: Final = re.compile(r"^[A-Za-z]:")
_MESSAGES: Final[dict[str, str]] = {
    "windows-path": (
        "{display} is a Windows path; on Linux and macOS it does not point to the intended file"
    ),
    "posix-path": (
        "{display} is a POSIX absolute path; on Windows it resolves against the current drive"
    ),
    "posix-api": (
        "{display} only exists on POSIX systems; on Windows it raises AttributeError or ImportError"
    ),
    "windows-api": (
        "{display} only exists on Windows; on Linux and macOS it raises "
        "AttributeError or ImportError"
    ),
}
_REMEDIATION: Final[dict[str, str]] = {
    "windows-path": (
        "Build paths with pathlib or os.path.join from portable parts, "
        "or guard Windows-only paths with a sys.platform check."
    ),
    "posix-path": (
        "Use tempfile, tmp_path or a configurable root instead of a fixed POSIX path, "
        "or guard it with a sys.platform check."
    ),
    "posix-api": (
        "Guard the code with a sys.platform or os.name check (or pytest.mark.skipif), "
        "or use a portable alternative."
    ),
    "windows-api": (
        "Guard the code with a sys.platform or os.name check (or pytest.mark.skipif), "
        "or use a portable alternative."
    ),
}


def classify_path_literal(text: str) -> str | None:
    """Return the kind of path a string literal denotes, if it is a platform-specific path.

    Args:
        text: The string literal to classify.

    Returns:
        ``"windows-path"`` for drive-qualified, UNC or backslash paths, ``"posix-path"``
        for absolute POSIX paths, and None for everything else.
    """
    if _DRIVE_RE.match(text) or text.startswith("\\\\") or "\\" in text:
        return "windows-path"
    if text.startswith("/"):
        return "posix-path"
    return None


def _leading_text(node: ast.expr) -> str | None:
    """Return the literal text of a string constant or of the first part of an f-string.

    Args:
        node: The argument expression.

    Returns:
        The string, or None when the expression does not start with a string literal.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _accessed_paths(call: ast.Call, imports: ImportTable) -> list[ast.expr]:
    """Return the path expressions that a call actually accesses on the file system.

    Building a path (``Path("/x")`` or ``os.path.join("C:\\\\", "x")`` on its own) is
    not an access; passing it to a file-system function or calling a ``Path``
    access method on it is.

    Args:
        call: The call expression.
        imports: The import table of the module containing ``call``.

    Returns:
        The arguments in path positions of a file-system function, or the
        receiver of a ``Path`` access method, or an empty list.
    """
    qualified = imports.resolve(call.func)
    if qualified is not None and qualified in FILE_SINKS:
        found: list[ast.expr] = []
        for position, keyword in FILE_SINKS[qualified]:
            argument = select_argument(call, position, keyword)
            if argument is not None:
                found.append(argument)
        return found
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in PATH_METHODS:
        receiver = func.value
        if isinstance(receiver, ast.BinOp) and isinstance(receiver.op, ast.Div):
            return [receiver]
        if isinstance(receiver, ast.Call) and imports.resolve(receiver.func) in PATH_CTORS:
            return [receiver]
    return []


def _path_literals(node: ast.expr, imports: ImportTable) -> list[ast.expr]:
    """Return the string literals that make up an accessed path expression.

    Args:
        node: The accessed path expression.
        imports: The import table of the module containing ``node``.

    Returns:
        String constants and f-strings found directly, as arguments of
        ``Path``/``PurePath``/``os.path.join`` calls, or as operands of ``/``.
    """
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        return [node]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _path_literals(node.left, imports) + _path_literals(node.right, imports)
    if isinstance(node, ast.Call) and imports.resolve(node.func) in _PATH_BUILDERS:
        found: list[ast.expr] = []
        for arg in node.args:
            if not isinstance(arg, ast.Starred):
                found.extend(_path_literals(arg, imports))
        return found
    return []


def _is_guard_handler(handler: ast.ExceptHandler, imports: ImportTable) -> bool:
    """Return True when an exception handler catches import or attribute failures.

    Args:
        handler: The exception handler.
        imports: The import table of the module containing the handler.

    Returns:
        True for a bare handler or one whose types include a guard exception.
    """
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(imports.resolve(item) in _GUARD_EXCEPTIONS for item in types)


def _mentions_type_checking(test: ast.expr, imports: ImportTable) -> bool:
    """Return True when an ``if`` test refers to ``typing.TYPE_CHECKING``.

    Args:
        test: The test expression of the ``if`` statement.
        imports: The import table of the module containing the statement.

    Returns:
        True when any name or attribute in ``test`` resolves to ``typing.TYPE_CHECKING``.
    """
    return any(
        isinstance(node, (ast.Name, ast.Attribute))
        and imports.resolve(node) == "typing.TYPE_CHECKING"
        for node in ast.walk(test)
    )


def _subtree(statements: list[ast.stmt]) -> set[ast.AST]:
    """Return every node inside a list of statements.

    Args:
        statements: The statements of a guarded block.

    Returns:
        All nodes of the statements, including the statements themselves.
    """
    return {node for statement in statements for node in ast.walk(statement)}


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A platform-specific construct found before guards are applied.

    Attributes:
        node: The reported node: a path literal, a Name or Attribute, or an import statement.
        kind: The finding kind, such as ``"windows-path"`` or ``"posix-api"``.
        display: The text shown in the message.
        value: The raw value stored in the metadata.
        chain: Frame identifiers of the enclosing scopes, innermost last.
        symbol: Dotted name of the enclosing function or class, if any.
    """

    node: ast.expr | ast.stmt
    kind: str
    display: str
    value: str
    chain: tuple[int, ...]
    symbol: str | None


class _PlatformVisitor(ast.NodeVisitor):
    """Collect platform-specific constructs and the scopes that check the platform."""

    def __init__(self, imports: ImportTable) -> None:
        """Create a visitor.

        Args:
            imports: The import table of the module being visited.
        """
        self._imports = imports
        self._names: list[str] = []
        self._frames: list[int] = []
        self._frame_count = 0
        self._checked: set[int] = set()
        self._suppressed: set[ast.AST] = set()
        self._reported_literals: set[ast.AST] = set()
        self.candidates: list[_Candidate] = []

    def is_suppressed(self, candidate: _Candidate) -> bool:
        """Return True when a candidate is excluded by a guard.

        Args:
            candidate: The candidate to check.

        Returns:
            True when the candidate sits in a guarded block or any enclosing scope
            checks the platform.
        """
        if candidate.node in self._suppressed:
            return True
        return any(frame in self._checked for frame in candidate.chain)

    def _enter_scope(self, node: ast.AST, name: str | None) -> None:
        """Visit a scope node with a new frame pushed, and the name pushed when given.

        Args:
            node: The scope node.
            name: The name of the function or class, or None for the module.
        """
        self._frame_count += 1
        self._frames.append(self._frame_count)
        if name is not None:
            self._names.append(name)
        self.generic_visit(node)
        if name is not None:
            self._names.pop()
        self._frames.pop()

    def _record(self, node: ast.expr | ast.stmt, kind: str, display: str, value: str) -> None:
        """Record one platform-specific construct in the current scope.

        Args:
            node: The reported node.
            kind: The finding kind.
            display: The text shown in the message.
            value: The raw value stored in the metadata.
        """
        self.candidates.append(
            _Candidate(
                node,
                kind,
                display,
                value,
                tuple(self._frames),
                ".".join(self._names) or None,
            )
        )

    def _visit_path_argument(self, argument: ast.expr) -> None:
        """Record a path-position argument when it is a platform-specific path literal.

        Args:
            argument: The argument expression.
        """
        text = _leading_text(argument)
        if text is None or argument in self._reported_literals:
            return
        kind = classify_path_literal(text)
        if kind is None:
            return
        self._reported_literals.add(argument)
        if isinstance(argument, ast.Constant):
            self._record(argument, kind, repr(text), text)
        else:
            rendered = ast.unparse(argument)
            self._record(argument, kind, rendered, rendered)

    def _record_module(self, node: ast.stmt, name: str) -> None:
        """Record an import of a module that exists on only one platform.

        Args:
            node: The import statement.
            name: The imported module name, possibly dotted.
        """
        top = name.split(".", maxsplit=1)[0]
        if top in POSIX_ONLY_MODULES:
            self._record(node, "posix-api", top, top)
        elif top in WINDOWS_ONLY_MODULES:
            self._record(node, "windows-api", top, top)

    def _visit_reference(self, node: ast.Name | ast.Attribute) -> None:
        """Record a loaded name that refers to a platform-only API or a platform check.

        Args:
            node: The name or attribute expression.
        """
        if not isinstance(node.ctx, ast.Load):
            return
        qualified = self._imports.resolve(node)
        if qualified is None:
            return
        if qualified in _PLATFORM_REFS:
            self._checked.add(self._frames[-1])
        if qualified in POSIX_ONLY_APIS:
            self._record(node, "posix-api", qualified, qualified)
        elif qualified in WINDOWS_ONLY_APIS:
            self._record(node, "windows-api", qualified, qualified)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        """Mark the body of a guarded ``try`` statement as suppressed, then visit it.

        Args:
            node: The try statement.
        """
        if any(_is_guard_handler(handler, self._imports) for handler in node.handlers):
            self._suppressed.update(_subtree(node.body))
        self.generic_visit(node)

    def visit_Module(self, node: ast.Module) -> None:
        """Visit the module scope."""
        self._enter_scope(node, None)

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
        """Record path arguments of path-taking calls and mark ``hasattr`` platform checks.

        Args:
            node: The call expression.
        """
        qualified = self._imports.resolve(node.func)
        if (
            qualified == "builtins.hasattr"
            and node.args
            and self._imports.resolve(node.args[0]) in _HASATTR_MODULES
        ):
            self._checked.add(self._frames[-1])
        for accessed in _accessed_paths(node, self._imports):
            for literal in _path_literals(accessed, self._imports):
                self._visit_path_argument(literal)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Record a loaded name that refers to a platform-only API or a platform check.

        Args:
            node: The name expression.
        """
        self._visit_reference(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Record a loaded attribute that refers to a platform-only API or a platform check.

        Args:
            node: The attribute expression.
        """
        self._visit_reference(node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Record imports of platform-only modules.

        Args:
            node: The import statement.
        """
        for alias in node.names:
            self._record_module(node, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record ``from`` imports of platform-only modules, skipping relative imports.

        Args:
            node: The import-from statement.
        """
        if node.level == 0 and node.module is not None:
            self._record_module(node, node.module)

    def visit_If(self, node: ast.If) -> None:
        """Suppress the body of an ``if`` that tests ``TYPE_CHECKING``, then visit it.

        Args:
            node: The if statement.
        """
        if _mentions_type_checking(node.test, self._imports):
            self._suppressed.update(_subtree(node.body))
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        """Visit a try statement, suppressing its body when a handler guards it.

        Args:
            node: The try statement.
        """
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        """Visit an exception-group try statement, suppressing its body when guarded.

        Args:
            node: The try statement.
        """
        self._visit_try(node)


def _build(module: ParsedModule, candidate: _Candidate) -> Finding:
    """Build the AR-PLAT-001 finding for one unguarded construct.

    Args:
        module: The parsed module containing the construct.
        candidate: The construct to report.

    Returns:
        The AR-PLAT-001 finding.
    """
    kind = candidate.kind
    node = candidate.node
    detail = f"{kind} {candidate.display}"
    location = SourceLocation(
        module.relpath,
        node.lineno,
        node.col_offset + 1,
        candidate.symbol,
    )
    return Finding(
        rule_id=RULE_ID,
        title=TITLE,
        message=_MESSAGES[kind].format(display=candidate.display),
        category=Category.PLATFORM,
        severity=Severity.WARNING,
        confidence=(Confidence.MEDIUM if kind == "posix-path" else Confidence.HIGH),
        fingerprint=compute_fingerprint(RULE_ID, module.relpath, candidate.symbol, detail),
        location=location,
        evidence=(
            Evidence(
                EvidenceKind.INFERRED,
                "ast",
                detail,
                location,
            ),
        ),
        remediation=_REMEDIATION[kind],
        failure_scenario=FAILURE_SCENARIO,
        metadata={"kind": kind, "value": candidate.value},
    )


class PlatformSpecificRule:
    """Report platform-specific paths, APIs and imports that lack a platform check.

    Attributes:
        rule_id: The rule identifier, ``AR-PLAT-001``.
        version: The rule version, bumped when findings change.
    """

    rule_id: str = RULE_ID
    version: str = RULE_VERSION

    def check(self, module: ParsedModule) -> list[Finding]:
        """Return one finding per platform-specific construct without a guard.

        Args:
            module: The parsed module to inspect.

        Returns:
            The findings for ``module``, in source order.
        """
        visitor = _PlatformVisitor(module.imports)
        visitor.visit(module.tree)
        return [
            _build(module, candidate)
            for candidate in visitor.candidates
            if not visitor.is_suppressed(candidate)
        ]
