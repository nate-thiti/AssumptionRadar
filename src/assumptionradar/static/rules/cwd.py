"""AR-CWD-001: relative paths accessed at run time.

A relative path literal passed to a file-system call, or used as the receiver
of a ``pathlib.Path`` access method, is resolved against the current working
directory when the code runs. The same code then reads or writes different
files depending on where it is launched. The rule reports such calls by parsing
the source only; the scanned code is never executed.
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

RULE_ID: Final = "AR-CWD-001"
RULE_VERSION: Final = "1"
TITLE: Final = "Relative path depends on the working directory"
REMEDIATION: Final = (
    "Build the path from the module location (Path(__file__).parent / ...), "
    "a pytest fixture such as tmp_path, or a configured root instead of the working directory."
)
FAILURE_SCENARIO: Final = (
    "Running from a different directory (an IDE, a CI job, `pytest path/to/tests`) "
    "raises FileNotFoundError or reads or writes the wrong file."
)

PATH_CTORS: Final[frozenset[str]] = frozenset(
    {"pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath"}
)
_PATH_BUILDERS: Final[frozenset[str]] = PATH_CTORS | frozenset({"os.path.join"})
PATH_METHODS: Final[frozenset[str]] = frozenset(
    {
        "open",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "exists",
        "is_file",
        "is_dir",
        "is_symlink",
        "stat",
        "lstat",
        "iterdir",
        "glob",
        "rglob",
        "mkdir",
        "rmdir",
        "unlink",
        "touch",
        "rename",
        "replace",
        "chmod",
        "samefile",
        "walk",
    }
)
_DIR_FD_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"dir_fd", "src_dir_fd", "dst_dir_fd", "root_dir"}
)
_FUNCTION_SINKS: Final[dict[str, tuple[tuple[int, str], ...]]] = {
    "builtins.open": ((0, "file"),),
    "io.open": ((0, "file"),),
    "codecs.open": ((0, "filename"),),
    "os.open": ((0, "path"),),
    "os.listdir": ((0, "path"),),
    "os.scandir": ((0, "path"),),
    "os.stat": ((0, "path"),),
    "os.lstat": ((0, "path"),),
    "os.remove": ((0, "path"),),
    "os.unlink": ((0, "path"),),
    "os.rmdir": ((0, "path"),),
    "os.mkdir": ((0, "path"),),
    "os.chdir": ((0, "path"),),
    "os.access": ((0, "path"),),
    "os.path.exists": ((0, "path"),),
    "os.path.lexists": ((0, "path"),),
    "os.path.isfile": ((0, "path"),),
    "os.path.isdir": ((0, "path"),),
    "os.path.islink": ((0, "path"),),
    "os.path.getsize": ((0, "path"),),
    "os.path.getmtime": ((0, "path"),),
    "os.path.getatime": ((0, "path"),),
    "os.path.getctime": ((0, "path"),),
    "shutil.rmtree": ((0, "path"),),
    "os.makedirs": ((0, "name"),),
    "tarfile.open": ((0, "name"),),
    "os.walk": ((0, "top"),),
    "shutil.copy": ((0, "src"), (1, "dst")),
    "shutil.copy2": ((0, "src"), (1, "dst")),
    "shutil.copyfile": ((0, "src"), (1, "dst")),
    "shutil.copytree": ((0, "src"), (1, "dst")),
    "shutil.move": ((0, "src"), (1, "dst")),
    "os.rename": ((0, "src"), (1, "dst")),
    "os.replace": ((0, "src"), (1, "dst")),
    "glob.glob": ((0, "pathname"),),
    "glob.iglob": ((0, "pathname"),),
    "sqlite3.connect": ((0, "database"),),
    "zipfile.ZipFile": ((0, "file"),),
}
_DRIVE_RE: Final = re.compile(r"^[A-Za-z]:")


def is_relative_path_literal(text: str) -> bool:
    """Return True when ``text`` is a path literal resolved against the working directory.

    Absolute paths, home-relative paths, URLs, drive-qualified paths and the
    special names ``NUL`` and ``:memory:`` are not relative file paths.

    Args:
        text: The string literal to check.

    Returns:
        True for a relative file path literal, False otherwise.
    """
    return not (
        text == ""
        or text.lower() in {"nul", ":memory:"}
        or text.startswith(("/", "\\", "~", "file:"))
        or _DRIVE_RE.match(text) is not None
        or "://" in text
    )


def _is_str_literal(node: ast.expr) -> bool:
    """Return True when ``node`` is a string constant that is a relative path literal."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and is_relative_path_literal(node.value)
    )


def _starts_relative(node: ast.Constant | ast.JoinedStr) -> bool:
    """Return True when a string constant, or the first part of an f-string, is relative."""
    if isinstance(node, ast.Constant):
        return _is_str_literal(node)
    return bool(node.values) and _is_str_literal(node.values[0])


def _call_base(call: ast.Call, imports: ImportTable) -> ast.Constant | ast.JoinedStr | None:
    """Return the relative base of a ``Path(...)`` or ``os.path.join(...)`` call.

    Args:
        call: The call expression.
        imports: The import table of the module containing ``call``.

    Returns:
        The relative base of the first positional argument, or None.
    """
    qualified = imports.resolve(call.func)
    if qualified is None or qualified not in _PATH_BUILDERS:
        return None
    if not call.args or isinstance(call.args[0], ast.Starred):
        return None
    return _relative_base(call.args[0], imports)


def _relative_base(node: ast.expr, imports: ImportTable) -> ast.Constant | ast.JoinedStr | None:
    """Return the literal node that makes ``node`` a relative path, if any.

    Args:
        node: The expression passed as a path.
        imports: The import table of the module containing ``node``.

    Returns:
        The relative string literal (or f-string whose first part is one), or None.
    """
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        return node if _starts_relative(node) else None
    if isinstance(node, ast.Call):
        return _call_base(node, imports)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _relative_base(node.left, imports)
    return None


def _select_argument(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    """Return the argument bound to a parameter, or None when it cannot be determined.

    Args:
        call: The call expression.
        position: Positional index of the parameter.
        keyword: Keyword name of the parameter.

    Returns:
        The argument expression, or None when it is absent or cannot be located
        because of star-arguments.
    """
    args = call.args
    if position < len(args) and not any(
        isinstance(arg, ast.Starred) for arg in args[: position + 1]
    ):
        return args[position]
    if any(isinstance(arg, ast.Starred) for arg in args):
        return None
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _function_hits(
    call: ast.Call, qualified: str, imports: ImportTable
) -> list[tuple[str, ast.expr]]:
    """Return the relative path arguments of a call to a known function sink.

    Args:
        call: The call expression.
        qualified: The qualified name of the called function.
        imports: The import table of the module containing ``call``.

    Returns:
        Pairs of sink name and offending argument expression.
    """
    if any(kw.arg in _DIR_FD_KEYWORDS for kw in call.keywords):
        return []
    sink = qualified.removeprefix("builtins.")
    hits: list[tuple[str, ast.expr]] = []
    for position, keyword in _FUNCTION_SINKS[qualified]:
        argument = _select_argument(call, position, keyword)
        if argument is not None and _relative_base(argument, imports) is not None:
            hits.append((sink, argument))
    return hits


def _is_path_receiver(node: ast.expr, imports: ImportTable) -> bool:
    """Return True when ``node`` is a ``Path(...)`` call or a ``/`` join expression."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return True
    if isinstance(node, ast.Call):
        return imports.resolve(node.func) in PATH_CTORS
    return False


def _call_hits(call: ast.Call, imports: ImportTable) -> list[tuple[str, ast.expr]]:
    """Return the sink name and offending argument for each relative path in one call.

    Args:
        call: The call expression.
        imports: The import table of the module containing ``call``.

    Returns:
        Pairs of sink name and offending argument expression.
    """
    qualified = imports.resolve(call.func)
    if qualified is not None and qualified in _FUNCTION_SINKS:
        return _function_hits(call, qualified, imports)
    if isinstance(call.func, ast.Attribute) and call.func.attr in PATH_METHODS:
        receiver = call.func.value
        if _is_path_receiver(receiver, imports) and _relative_base(receiver, imports) is not None:
            return [(f"pathlib.Path.{call.func.attr}", receiver)]
    return []


@dataclass(frozen=True, slots=True)
class _Hit:
    """One offending call argument found by the visitor.

    Attributes:
        call: The call expression containing the argument.
        sink: Sink name used in messages and metadata.
        argument: The offending argument expression.
        symbol: Dotted name of the enclosing function or class, if any.
    """

    call: ast.Call
    sink: str
    argument: ast.expr
    symbol: str | None


class _SinkVisitor(ast.NodeVisitor):
    """Collect relative path arguments of sink calls, tracking the enclosing scope."""

    def __init__(self, imports: ImportTable) -> None:
        """Create a visitor.

        Args:
            imports: The import table of the module being visited.
        """
        self._imports = imports
        self._scope: list[str] = []
        self.hits: list[_Hit] = []

    def _visit_scope(self, node: ast.AST, name: str) -> None:
        """Visit the children of a scope node with ``name`` pushed on the scope stack."""
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit a function definition."""
        self._visit_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit an async function definition."""
        self._visit_scope(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit a class definition."""
        self._visit_scope(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        """Record relative path arguments of a sink call, then visit its children."""
        symbol = ".".join(self._scope) or None
        for sink, argument in _call_hits(node, self._imports):
            self.hits.append(_Hit(node, sink, argument, symbol))
        self.generic_visit(node)


def _build_finding(module: ParsedModule, hit: _Hit) -> Finding:
    """Build the finding for one offending argument.

    Args:
        module: The parsed module containing the call.
        hit: The offending argument.

    Returns:
        The AR-CWD-001 finding.
    """
    argument = hit.argument
    base = _relative_base(argument, module.imports)
    if isinstance(argument, ast.Constant):
        path_text = str(argument.value)
        display = repr(argument.value)
    else:
        path_text = ast.unparse(argument)
        display = path_text
    sink = hit.sink
    location = SourceLocation(
        module.relpath,
        hit.call.lineno,
        hit.call.col_offset + 1,
        hit.symbol,
    )
    return Finding(
        rule_id=RULE_ID,
        title=TITLE,
        message=(
            f"{sink}() is called with the relative path {display}, "
            "which is resolved against the current working directory"
        ),
        category=Category.CWD,
        severity=Severity.WARNING,
        confidence=(Confidence.MEDIUM if isinstance(base, ast.JoinedStr) else Confidence.HIGH),
        fingerprint=compute_fingerprint(RULE_ID, module.relpath, hit.symbol, f"{sink} {display}"),
        location=location,
        evidence=(
            Evidence(
                EvidenceKind.INFERRED,
                "ast",
                f"{sink}() receives a relative path literal",
                location,
            ),
        ),
        remediation=REMEDIATION,
        failure_scenario=FAILURE_SCENARIO,
        metadata={"sink": sink, "path": path_text},
    )


class RelativePathAccessRule:
    """Report relative path literals passed to file-system calls.

    Attributes:
        rule_id: The rule identifier, ``AR-CWD-001``.
        version: The rule version, bumped when findings change.
    """

    rule_id: str = RULE_ID
    version: str = RULE_VERSION

    def check(self, module: ParsedModule) -> list[Finding]:
        """Return one finding per relative path argument of a sink call.

        Args:
            module: The parsed module to inspect.

        Returns:
            The findings for ``module``, in source order.
        """
        visitor = _SinkVisitor(module.imports)
        visitor.visit(module.tree)
        return [_build_finding(module, hit) for hit in visitor.hits]
