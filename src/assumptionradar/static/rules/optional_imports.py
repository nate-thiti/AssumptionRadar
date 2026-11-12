"""AR-IMP-001: optional imports whose distribution the project does not declare.

The rule reports imports guarded by ``try``/``except ImportError`` (and similar
handlers), literal ``importlib.import_module`` calls in such guards, and
literal ``pytest.importorskip`` calls, when no dependency, extra or dependency
group in ``pyproject.toml`` declares the distribution. The rule parses the
source only; the scanned code is never executed.
"""

from __future__ import annotations

import ast
import hashlib
import sys
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
from assumptionradar.static.project import normalize_distribution

RULE_ID: Final = "AR-IMP-001"
RULE_VERSION: Final = "1"
TITLE: Final = "Optional dependency is not declared"
REMEDIATION_TEMPLATE: Final = (
    "Add {distribution} to an extra under [project.optional-dependencies] "
    "or to a [dependency-groups] group so the environments that need it install it, "
    "or remove the optional import."
)
FAILURE_SCENARIO: Final = (
    "Environments built from the declared dependencies never install the package, "
    "so the optional code path or the skipped tests never run there and failures go unnoticed."
)
_TRY_IMPORT: Final = "try-import"
_IMPORTORSKIP: Final = "importorskip"
_IMPORTLIB_IMPORT: Final = "importlib.import_module"
_PYTEST_IMPORTORSKIP: Final = "pytest.importorskip"
_GUARD_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "builtins.ImportError",
        "builtins.ModuleNotFoundError",
        "builtins.Exception",
        "builtins.BaseException",
    }
)

IMPORT_DISTRIBUTION_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "yaml": ("pyyaml",),
    "PIL": ("pillow",),
    "sklearn": ("scikit-learn",),
    "cv2": ("opencv-python", "opencv-python-headless"),
    "bs4": ("beautifulsoup4",),
    "dateutil": ("python-dateutil",),
    "dotenv": ("python-dotenv",),
    "jwt": ("pyjwt",),
    "attr": ("attrs",),
    "google.protobuf": ("protobuf",),
    "OpenSSL": ("pyopenssl",),
    "serial": ("pyserial",),
    "Crypto": ("pycryptodome",),
    "magic": ("python-magic",),
    "docx": ("python-docx",),
    "pptx": ("python-pptx",),
    "fitz": ("pymupdf",),
    "git": ("gitpython",),
    "zmq": ("pyzmq",),
    "usb": ("pyusb",),
    "win32api": ("pywin32",),
    "win32con": ("pywin32",),
    "pywintypes": ("pywin32",),
    "MySQLdb": ("mysqlclient",),
    "psycopg2": ("psycopg2", "psycopg2-binary"),
    "skimage": ("scikit-image",),
    "Levenshtein": ("python-levenshtein",),
    "xdist": ("pytest-xdist",),
    "pytest_cov": ("pytest-cov",),
    "nacl": ("pynacl",),
    "socks": ("pysocks",),
    "gi": ("pygobject",),
    "wx": ("wxpython",),
    "pkg_resources": ("setuptools",),
    "multipart": ("python-multipart",),
    "jose": ("python-jose",),
    "ldap": ("python-ldap",),
    "Xlib": ("python-xlib",),
}


def distribution_candidates(module: str) -> frozenset[str]:
    """Return the distribution names that could provide an imported module.

    Args:
        module: A dotted module name as written in the import.

    Returns:
        The normalized top-level name, the aliases of the top-level name, and the
        aliases of the first two dotted parts.
    """
    parts = module.split(".")
    top = parts[0]
    result = {normalize_distribution(top), *IMPORT_DISTRIBUTION_ALIASES.get(top, ())}
    result.update(IMPORT_DISTRIBUTION_ALIASES.get(".".join(parts[:2]), ()))
    return frozenset(result)


def _distribution(module: str) -> str:
    """Return the distribution name to suggest for an imported module.

    Args:
        module: A dotted module name as written in the import.

    Returns:
        The first alias of the first two dotted parts, else the first alias of the
        top-level name, else the normalized top-level name.
    """
    parts = module.split(".")
    top = parts[0]
    pair = ".".join(parts[:2])
    if pair in IMPORT_DISTRIBUTION_ALIASES:
        return IMPORT_DISTRIBUTION_ALIASES[pair][0]
    if top in IMPORT_DISTRIBUTION_ALIASES:
        return IMPORT_DISTRIBUTION_ALIASES[top][0]
    return normalize_distribution(top)


def _literal_arg(call: ast.Call, keyword: str | None = None) -> str | None:
    """Return the string value of the first argument of a call, if it is a literal.

    Args:
        call: The call expression.
        keyword: Optional keyword name used when there is no positional argument.

    Returns:
        The string value, or None when the argument is missing or not a string literal.
    """
    value: ast.expr | None = call.args[0] if call.args else None
    if value is None and keyword is not None:
        for item in call.keywords:
            if item.arg == keyword:
                value = item.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """An optional import found in a module.

    Attributes:
        module: The imported dotted module name.
        how: ``"try-import"`` or ``"importorskip"``.
        node: The statement or call that locates the import.
        symbol: Dotted name of the enclosing function or class, if any.
    """

    module: str
    how: str
    node: ast.stmt | ast.expr
    symbol: str | None


class _ImportVisitor(ast.NodeVisitor):
    """Collect optional import candidates, tracking enclosing scopes and guards."""

    def __init__(self, imports: ImportTable) -> None:
        """Create a visitor.

        Args:
            imports: The import table of the module being visited.
        """
        self._imports = imports
        self._names: list[str] = []
        self._guard = 0
        self.candidates: list[_Candidate] = []

    def _symbol(self) -> str | None:
        """Return the dotted name of the enclosing def or class, if any."""
        return ".".join(self._names) or None

    def _add(self, node: ast.stmt | ast.expr, module: str, how: str) -> None:
        """Record a candidate at the current scope."""
        self.candidates.append(_Candidate(module, how, node, self._symbol()))

    def _enter_scope(self, node: ast.AST, name: str) -> None:
        """Visit the children of a def or class node with ``name`` pushed on the scope stack."""
        self._names.append(name)
        self.generic_visit(node)
        self._names.pop()

    def _is_guarding_handler(self, handler: ast.ExceptHandler) -> bool:
        """Return True when an ``except`` clause catches a failed import."""
        if handler.type is None:
            return True
        candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        return any(self._imports.resolve(item) in _GUARD_EXCEPTIONS for item in candidates)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        """Visit a try statement, marking imports in a guarding body as optional."""
        guarded = any(self._is_guarding_handler(handler) for handler in node.handlers)
        if guarded:
            self._guard += 1
        for stmt in node.body:
            self.visit(stmt)
        if guarded:
            self._guard -= 1
        for handler in node.handlers:
            self.visit(handler)
        for stmt in node.orelse:
            self.visit(stmt)
        for stmt in node.finalbody:
            self.visit(stmt)

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

    def visit_Import(self, node: ast.Import) -> None:
        """Record the modules of an import statement inside a guarded body."""
        if self._guard:
            for alias in node.names:
                self._add(node, alias.name, _TRY_IMPORT)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record the module of an absolute from-import inside a guarded body."""
        if self._guard and node.level == 0 and node.module is not None:
            self._add(node, node.module, _TRY_IMPORT)

    def visit_Call(self, node: ast.Call) -> None:
        """Record literal ``importlib.import_module`` and ``pytest.importorskip`` calls."""
        resolved = self._imports.resolve(node.func)
        if resolved == _IMPORTLIB_IMPORT and self._guard:
            module = _literal_arg(node)
            if module is not None:
                self._add(node, module, _TRY_IMPORT)
        elif resolved == _PYTEST_IMPORTORSKIP:
            module = _literal_arg(node, "modname")
            if module is not None:
                self._add(node, module, _IMPORTORSKIP)
        self.generic_visit(node)


def _message(module: str, how: str) -> str:
    """Return the finding message for a candidate.

    Args:
        module: The imported dotted module name.
        how: ``"try-import"`` or ``"importorskip"``.

    Returns:
        The message text.
    """
    if how == _TRY_IMPORT:
        return (
            f"{module!r} is imported as an optional dependency, but no dependency, "
            "extra or dependency group in pyproject.toml declares it"
        )
    return (
        f"{_PYTEST_IMPORTORSKIP}({module!r}) skips the tests when {module!r} is missing, "
        "but no dependency, extra or dependency group in pyproject.toml declares it"
    )


def _build(module: ParsedModule, candidate: _Candidate) -> Finding:
    """Build the AR-IMP-001 finding for one undeclared optional import.

    Args:
        module: The parsed module containing the import.
        candidate: The optional import.

    Returns:
        The AR-IMP-001 finding.
    """
    distribution = _distribution(candidate.module)
    how = candidate.how
    location = SourceLocation(
        module.relpath,
        candidate.node.lineno,
        candidate.node.col_offset + 1,
        candidate.symbol,
    )
    return Finding(
        rule_id=RULE_ID,
        title=TITLE,
        message=_message(candidate.module, how),
        category=Category.IMPORTS,
        severity=Severity.WARNING,
        confidence=Confidence.MEDIUM,
        fingerprint=compute_fingerprint(
            RULE_ID, module.relpath, candidate.symbol, f"{how} {candidate.module}"
        ),
        location=location,
        evidence=(
            Evidence(
                EvidenceKind.INFERRED,
                "ast",
                f"{how} of {candidate.module!r}",
                location,
            ),
        ),
        remediation=REMEDIATION_TEMPLATE.format(distribution=distribution),
        failure_scenario=FAILURE_SCENARIO,
        metadata={
            "module": candidate.module,
            "how": how,
            "distribution": distribution,
        },
    )


class OptionalImportRule:
    """Report optional imports whose distribution no project metadata declares.

    Attributes:
        rule_id: The rule identifier, ``AR-IMP-001``.
        version: ``"1"`` with no project metadata, else ``"1+"`` and a digest of it.
        declared: Normalized distribution names the project declares.
        first_party: Importable top-level names inside the project.
    """

    rule_id: str = RULE_ID

    def __init__(self, declared: Iterable[str] = (), first_party: Iterable[str] = ()) -> None:
        """Create the rule.

        Args:
            declared: Distribution names the project declares.
            first_party: Importable top-level names inside the project.
        """
        self.declared: frozenset[str] = frozenset(normalize_distribution(d) for d in declared)
        self.first_party: frozenset[str] = frozenset(first_party)
        if not self.declared and not self.first_party:
            self.version: str = RULE_VERSION
        else:
            payload = "\n".join(sorted(self.declared)) + "\0" + "\n".join(sorted(self.first_party))
            digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
            self.version = f"{RULE_VERSION}+{digest}"

    def _is_skipped(self, module: str) -> bool:
        """Return True when an import must not be reported.

        Args:
            module: The imported dotted module name.

        Returns:
            True for standard library, ``__future__``, first-party and declared modules.
        """
        top = module.split(".", maxsplit=1)[0]
        if top == "__future__" or top in sys.stdlib_module_names:
            return True
        if top in self.first_party:
            return True
        return not distribution_candidates(module).isdisjoint(self.declared)

    def check(self, module: ParsedModule) -> list[Finding]:
        """Return one finding per undeclared optional import in the module.

        Args:
            module: The parsed module to inspect.

        Returns:
            The findings for ``module``, in source order.
        """
        visitor = _ImportVisitor(module.imports)
        visitor.visit(module.tree)
        return [
            _build(module, candidate)
            for candidate in visitor.candidates
            if not self._is_skipped(candidate.module)
        ]
