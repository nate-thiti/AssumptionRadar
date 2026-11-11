"""AR-ENV-001 (required env var) and AR-ENV-002 (undeclared env var).

AR-ENV-001 reports reads of ``os.environ`` that raise ``KeyError`` when the
variable is not set, unless a check for the same key exists in the read's own
or an enclosing scope, or the read is protected by an ``except`` clause of the
same scope. AR-ENV-002 reports literal environment variable names that a file
reads but that the caller has not declared. The rules parse the source only;
the scanned code is never executed.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable
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

REQUIRED_RULE_ID: Final = "AR-ENV-001"
UNDECLARED_RULE_ID: Final = "AR-ENV-002"
RULE_VERSION: Final = "1"
REQUIRED_TITLE: Final = "Required environment variable has no default"
REQUIRED_REMEDIATION: Final = (
    "Use os.environ.get(name, default) or os.getenv(name, default), "
    "or check for the variable and fail with a clear message; "
    "in tests, set it with monkeypatch.setenv."
)
REQUIRED_FAILURE: Final = (
    "A machine, CI job or fresh shell without the variable raises KeyError "
    "before the code does anything useful."
)
UNDECLARED_TITLE: Final = "Undeclared environment variable"
UNDECLARED_REMEDIATION: Final = (
    "Declare the variable in the project configuration so the scanner knows "
    "it is an intended dependency, or remove the read."
)
UNDECLARED_FAILURE: Final = (
    "A machine or CI job without the variable behaves differently from a developer machine."
)

_ENVIRON: Final = "os.environ"
_GETENV: Final = "os.getenv"
_GUARD_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "builtins.KeyError",
        "builtins.LookupError",
        "builtins.Exception",
        "builtins.BaseException",
    }
)


def _str_value(node: ast.expr) -> str | None:
    """Return the value of a string constant node, or None for any other node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _key_text(node: ast.expr) -> str:
    """Return the key text: the string value of a literal, else the unparsed expression."""
    value = _str_value(node)
    return value if value is not None else ast.unparse(node)


def _call_key(call: ast.Call) -> ast.expr | None:
    """Return the key argument of a ``get``/``setdefault``/``getenv`` call, if any.

    Args:
        call: The call expression.

    Returns:
        The first positional argument, else the ``key`` keyword value, else None.
    """
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "key":
            return keyword.value
    return None


@dataclass(frozen=True, slots=True)
class _Site:
    """A literal-key read of an environment variable.

    Attributes:
        node: The Subscript, Call or Compare node that performs the read.
        key: The key expression (a string constant).
        symbol: Dotted name of the enclosing function or class, if any.
    """

    node: ast.expr
    key: ast.expr
    symbol: str | None


@dataclass(frozen=True, slots=True)
class _Required:
    """A read that raises ``KeyError`` when the variable is not set.

    Attributes:
        node: The Subscript or Call node that performs the read.
        key: The key expression, literal or not.
        access: ``"subscript"`` or ``"pop"``.
        symbol: Dotted name of the enclosing function or class, if any.
        chain: Identifiers of the enclosing scopes, innermost last.
        protected: True when the read is inside a guarding ``try`` body of its scope.
    """

    node: ast.expr
    key: ast.expr
    access: str
    symbol: str | None
    chain: tuple[int, ...]
    protected: bool


@dataclass(slots=True)
class _Frame:
    """A scope on the visitor stack.

    Attributes:
        ident: Unique identifier of the scope within the module.
        protected: Number of guarding ``try`` bodies currently entered in this scope.
    """

    ident: int
    protected: int = 0


class _EnvVisitor(ast.NodeVisitor):
    """Collect environment reads, checks and required reads, tracking scopes."""

    def __init__(self, imports: ImportTable) -> None:
        """Create a visitor.

        Args:
            imports: The import table of the module being visited.
        """
        self._imports = imports
        self._names: list[str] = []
        self._frames: list[_Frame] = []
        self._frame_count = 0
        self.sites: list[_Site] = []
        self.required: list[_Required] = []
        self.checks: dict[int, set[str]] = {}

    def _push_frame(self) -> None:
        """Push a new scope frame."""
        self._frame_count += 1
        self._frames.append(_Frame(self._frame_count))

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

    def _is_environ(self, node: ast.expr) -> bool:
        """Return True when ``node`` refers to ``os.environ``."""
        return self._imports.resolve(node) == _ENVIRON

    def _add_site(self, node: ast.expr, key: ast.expr) -> None:
        """Record a read site when the key is a string literal."""
        if _str_value(key) is not None:
            self.sites.append(_Site(node, key, self._symbol()))

    def _add_check(self, key: ast.expr) -> None:
        """Record a check of ``key`` in the innermost scope."""
        self.checks.setdefault(self._frames[-1].ident, set()).add(_key_text(key))

    def _add_required(self, node: ast.expr, key: ast.expr, access: str) -> None:
        """Record a read that raises ``KeyError`` when the variable is not set."""
        self.required.append(
            _Required(
                node,
                key,
                access,
                self._symbol(),
                tuple(frame.ident for frame in self._frames),
                self._frames[-1].protected > 0,
            )
        )

    def _is_guarding_handler(self, handler: ast.ExceptHandler) -> bool:
        """Return True when an ``except`` clause catches a missing key."""
        if handler.type is None:
            return True
        candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        for candidate in candidates:
            qualified = self._imports.resolve(candidate)
            if qualified is not None and qualified in _GUARD_EXCEPTIONS:
                return True
        return False

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        """Visit a try statement, marking reads in a guarding body as protected."""
        frame = self._frames[-1]
        guarded = any(self._is_guarding_handler(handler) for handler in node.handlers)
        if guarded:
            frame.protected += 1
        for stmt in node.body:
            self.visit(stmt)
        if guarded:
            frame.protected -= 1
        for handler in node.handlers:
            self.visit(handler)
        for stmt in node.orelse:
            self.visit(stmt)
        for stmt in node.finalbody:
            self.visit(stmt)

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

    def visit_Try(self, node: ast.Try) -> None:
        """Visit a try statement."""
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        """Visit an exception-group try statement."""
        self._visit_try(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Record subscript reads and writes of ``os.environ``."""
        if self._is_environ(node.value):
            if isinstance(node.ctx, ast.Load):
                self._add_site(node, node.slice)
                self._add_required(node, node.slice, "subscript")
            elif isinstance(node.ctx, ast.Store):
                self._add_check(node.slice)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Record reads, checks and required pops performed by a call."""
        func = node.func
        key = _call_key(node)
        if isinstance(func, ast.Attribute):
            if self._is_environ(func.value):
                if func.attr in ("get", "setdefault"):
                    if key is not None:
                        self._add_site(node, key)
                        self._add_check(key)
                elif func.attr == "pop" and len(node.args) == 1 and not node.keywords:
                    self._add_required(node, node.args[0], "pop")
            if func.attr == "setenv" and node.args:
                self._add_check(node.args[0])
        if self._imports.resolve(func) == _GETENV and key is not None:
            self._add_site(node, key)
            self._add_check(key)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        """Record ``key in os.environ`` and ``key not in os.environ`` tests."""
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and self._is_environ(node.comparators[0])
        ):
            self._add_site(node, node.left)
            self._add_check(node.left)
        self.generic_visit(node)


def _scan(module: ParsedModule) -> _EnvVisitor:
    """Run the visitor over one parsed module.

    Args:
        module: The parsed module to inspect.

    Returns:
        The visitor holding the sites, required reads and checks of the module.
    """
    visitor = _EnvVisitor(module.imports)
    visitor.visit(module.tree)
    return visitor


def _is_guarded(read: _Required, checks: dict[int, set[str]]) -> bool:
    """Return True when a required read is guarded and must not be reported.

    Args:
        read: The required read.
        checks: Checked key texts per scope identifier.

    Returns:
        True when the read is protected by a try body or a check in its scope chain.
    """
    if read.protected:
        return True
    key = _key_text(read.key)
    return any(ident in checks and key in checks[ident] for ident in read.chain)


def collect_env_reads(module: ParsedModule) -> dict[str, list[SourceLocation]]:
    """Return the literal-key environment read sites of a module.

    Args:
        module: The parsed module to inspect.

    Returns:
        Variable name to read locations, names sorted, each list sorted by line and column.
    """
    grouped: dict[str, list[tuple[int, int, SourceLocation]]] = {}
    for site in _scan(module).sites:
        node = site.node
        location = SourceLocation(module.relpath, node.lineno, node.col_offset + 1, site.symbol)
        grouped.setdefault(_key_text(site.key), []).append((node.lineno, node.col_offset, location))
    return {
        name: [location for _, _, location in sorted(grouped[name], key=lambda t: t[:2])]
        for name in sorted(grouped)
    }


def _build_required(module: ParsedModule, read: _Required) -> Finding:
    """Build the AR-ENV-001 finding for one unguarded required read.

    Args:
        module: The parsed module containing the read.
        read: The required read.

    Returns:
        The AR-ENV-001 finding.
    """
    key_text = _key_text(read.key)
    literal = _str_value(read.key) is not None
    display = repr(key_text) if literal else ast.unparse(read.key)
    how = "os.environ[...]" if read.access == "subscript" else "os.environ.pop()"
    location = SourceLocation(
        module.relpath, read.node.lineno, read.node.col_offset + 1, read.symbol
    )
    return Finding(
        rule_id=REQUIRED_RULE_ID,
        title=REQUIRED_TITLE,
        message=(
            f"Environment variable {display} is read with {how} and no default; "
            "it raises KeyError when the variable is not set"
        ),
        category=Category.ENVIRONMENT,
        severity=Severity.WARNING,
        confidence=Confidence.HIGH if literal else Confidence.MEDIUM,
        fingerprint=compute_fingerprint(
            REQUIRED_RULE_ID, module.relpath, read.symbol, f"{read.access} {display}"
        ),
        location=location,
        evidence=(
            Evidence(
                EvidenceKind.INFERRED,
                "ast",
                f"{how} read of {display} with no default or guard",
                location,
            ),
        ),
        remediation=REQUIRED_REMEDIATION,
        failure_scenario=REQUIRED_FAILURE,
        metadata={"name": key_text, "access": read.access},
    )


class RequiredEnvVarRule:
    """Report environment variable reads that have no default and no guard.

    Attributes:
        rule_id: The rule identifier, ``AR-ENV-001``.
        version: The rule version, bumped when findings change.
    """

    rule_id: str = REQUIRED_RULE_ID
    version: str = RULE_VERSION

    def check(self, module: ParsedModule) -> list[Finding]:
        """Return one finding per unguarded required read.

        Args:
            module: The parsed module to inspect.

        Returns:
            The findings for ``module``, in source order.
        """
        visitor = _scan(module)
        return [
            _build_required(module, read)
            for read in visitor.required
            if not _is_guarded(read, visitor.checks)
        ]


class UndeclaredEnvVarRule:
    """Report literal environment variable names that are read but not declared.

    Attributes:
        rule_id: The rule identifier, ``AR-ENV-002``.
        version: ``"1"`` with no declared names, else ``"1+"`` and a digest of the set.
        declared: The declared variable names.
    """

    rule_id: str = UNDECLARED_RULE_ID

    def __init__(self, declared: Iterable[str] = ()) -> None:
        """Create the rule.

        Args:
            declared: Variable names the project declares as intended dependencies.
        """
        self.declared: frozenset[str] = frozenset(declared)
        if not self.declared:
            self.version: str = RULE_VERSION
        else:
            digest = hashlib.sha256("\n".join(sorted(self.declared)).encode()).hexdigest()[:12]
            self.version = f"{RULE_VERSION}+{digest}"

    def check(self, module: ParsedModule) -> list[Finding]:
        """Return one finding per undeclared variable name read in the module.

        Args:
            module: The parsed module to inspect.

        Returns:
            The findings for ``module``, ordered by variable name.
        """
        findings: list[Finding] = []
        for name, locations in collect_env_reads(module).items():
            if name in self.declared:
                continue
            count = len(locations)
            location = locations[0]
            noun = "read" if count == 1 else "reads"
            findings.append(
                Finding(
                    rule_id=UNDECLARED_RULE_ID,
                    title=UNDECLARED_TITLE,
                    message=(
                        f"Environment variable {name!r} is not declared "
                        f"({count} {noun} in this file)"
                    ),
                    category=Category.ENVIRONMENT,
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    fingerprint=compute_fingerprint(UNDECLARED_RULE_ID, module.relpath, None, name),
                    location=location,
                    evidence=(
                        Evidence(
                            EvidenceKind.INFERRED,
                            "ast",
                            f"{count} read(s) of {name!r}",
                            location,
                        ),
                    ),
                    remediation=UNDECLARED_REMEDIATION,
                    failure_scenario=UNDECLARED_FAILURE,
                    metadata={"name": name, "reads": str(count)},
                )
            )
        return findings
