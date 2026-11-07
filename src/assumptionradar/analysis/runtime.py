"""Runtime observer built on ``sys.addaudithook`` (ADR-0002).

While an observer is active, selected filesystem, environment, DNS, network and
subprocess events are recorded and attributed to the nearest project frame.
Secret values, command arguments and request data are never stored.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import FrameType, MappingProxyType, TracebackType
from typing import Final, Self

import assumptionradar
from assumptionradar.core.exceptions import ObserverError
from assumptionradar.core.models import SourceLocation
from assumptionradar.utils.redact import redact_text


class EventKind(StrEnum):
    """Kinds of runtime events that an observer can record."""

    FILE_OPEN = "file_open"
    DIR_LIST = "dir_list"
    DIR_CREATE = "dir_create"
    FILE_REMOVE = "file_remove"
    FILE_RENAME = "file_rename"
    CHDIR = "chdir"
    ENV_READ = "env_read"
    ENV_WRITE = "env_write"
    DNS_LOOKUP = "dns_lookup"
    NETWORK_CONNECT = "network_connect"
    SUBPROCESS = "subprocess"


HANDLED_AUDIT_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "open",
        "os.listdir",
        "os.scandir",
        "os.mkdir",
        "os.remove",
        "os.rmdir",
        "os.rename",
        "os.chdir",
        "os.putenv",
        "os.unsetenv",
        "socket.getaddrinfo",
        "socket.connect",
        "subprocess.Popen",
        "os.system",
        "os.spawn",
    }
)

MAX_ERRORS: Final[int] = 20

_EXCLUDED_PARTS: Final[frozenset[str]] = frozenset(
    {".venv", "venv", "env", ".tox", ".nox", "site-packages", "dist-packages", "__pycache__"}
)
_PACKAGE_DIR: Final[str] = os.path.normcase(
    os.path.abspath(Path(str(assumptionradar.__file__)).parent)  # noqa: PTH100
)
_MIN_ADDRESS_PARTS: Final[int] = 2
_IMPORT_FRAME_PREFIX: Final[str] = "<frozen importlib"

Described = tuple[EventKind, str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class ObservedEvent:
    """A single runtime event recorded by a :class:`RuntimeObserver`.

    Attributes:
        kind: The category of the event.
        target: Redacted subject of the event (path, key, host, program).
        attributes: Extra non-sensitive details, stored read-only and sorted.
        location: Project source location that caused the event, if known.
        test_id: Test identifier set via ``RuntimeObserver.set_test``, if any.
    """

    kind: EventKind
    target: str
    attributes: Mapping[str, str] = field(hash=False)
    location: SourceLocation | None
    test_id: str | None

    def __post_init__(self) -> None:
        """Store attributes as a sorted, read-only mapping."""
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(sorted(dict(self.attributes).items()))),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "kind": str(self.kind),
            "target": self.target,
            "attributes": dict(self.attributes),
            "location": self.location.to_dict() if self.location is not None else None,
            "test_id": self.test_id,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    """A snapshot of everything an observer recorded.

    Attributes:
        events: Recorded events in arrival order.
        dropped: Events discarded because the buffer was full.
        unattributed: Events with no project frame, when those are not kept.
        errors: Bounded list of internal handler errors.
    """

    events: tuple[ObservedEvent, ...]
    dropped: int
    unattributed: int
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed keys."""
        return {
            "events": [event.to_dict() for event in self.events],
            "dropped": self.dropped,
            "unattributed": self.unattributed,
            "errors": list(self.errors),
        }


# Module-level state. The audit hook cannot be removed once installed, so it is
# installed at most once per process and dispatches to whichever observer is active.
_ACTIVE: RuntimeObserver | None = None
_ACTIVATION_LOCK: Final = threading.Lock()
_HOOK_INSTALLED: bool = False
_GUARD: Final = threading.local()


def _guard_busy() -> bool:
    """Return True when the current thread is already handling an event."""
    return bool(getattr(_GUARD, "busy", False))


def _text(value: object) -> str:
    """Convert a path-like, bytes or other value to text."""
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, bytes):
        return os.fsdecode(value)
    return str(value)


def _as_path(value: object) -> str | None:
    """Return path text for str, bytes or PathLike values; None for fds and others."""
    if value is None or isinstance(value, int):
        return None
    if isinstance(value, (str, bytes, os.PathLike)):
        return _text(value)
    return None


def _arg(args: tuple[object, ...], index: int) -> object:
    """Return the audit argument at ``index`` or None when absent."""
    return args[index] if index < len(args) else None


def _relative(path: str) -> str:
    """Return the ``relative`` attribute value for a path."""
    return "false" if os.path.isabs(path) else "true"  # noqa: PTH117


def _normalize_mode(mode: str) -> str:
    """Strip binary, text and update markers from an open mode string."""
    cleaned = "".join(char for char in mode if char not in "bt+")
    return cleaned or "r"


def _split_command(command: str) -> list[str]:
    """Split a command line into tokens, honouring double quotes on Windows."""
    if os.name == "nt":
        tokens: list[str] = []
        current: list[str] = []
        in_quotes = False
        has_token = False
        for char in command:
            if char == '"':
                in_quotes = not in_quotes
                has_token = True
            elif char.isspace() and not in_quotes:
                if has_token:
                    tokens.append("".join(current))
                    current = []
                    has_token = False
            else:
                current.append(char)
                has_token = True
        if has_token:
            tokens.append("".join(current))
        return tokens
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _argv_from(value: object) -> list[str]:
    """Return argv tokens from a list/tuple argument or a command-line string."""
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value]
    if isinstance(value, (str, bytes)):
        return _split_command(_text(value))
    return []


def _from_import_system(frame: FrameType | None) -> bool:
    """Return True when any frame on the stack belongs to the import system."""
    current = frame
    while current is not None:
        if current.f_code.co_filename.startswith(_IMPORT_FRAME_PREFIX):
            return True
        current = current.f_back
    return False


def _is_within(path: str, base: str) -> bool:
    """Return True when normalised ``path`` lies under normalised ``base``."""
    if path == base:
        return True
    prefix = base if base.endswith(os.sep) else base + os.sep
    return path.startswith(prefix)


def format_address(address: object) -> str:
    """Format a socket address as ``host:port`` (IPv6 hosts in brackets).

    Args:
        address: A socket address tuple, bytes, or any other object.

    Returns:
        A readable address string. Unknown forms use ``str``.
    """
    if isinstance(address, tuple) and len(address) >= _MIN_ADDRESS_PARTS:
        host = _text(address[0])
        port = address[1]
        if ":" in host:
            return f"[{host}]:{port}"
        return f"{host}:{port}"
    if isinstance(address, bytes):
        return os.fsdecode(address)
    return str(address)


def _process_description(
    program: str, argv: list[str], *, env_override: bool, shell: bool
) -> Described:
    """Build a SUBPROCESS description. Only the program name and counts are kept."""
    attributes = {
        "path_lookup": "false" if ("/" in program or "\\" in program) else "true",
        "argc": str(len(argv)),
        "shell": "true" if shell else "false",
        "env_override": "true" if env_override else "false",
    }
    return EventKind.SUBPROCESS, redact_text(program), attributes


def _describe_open(args: tuple[object, ...]) -> Described | None:
    """Describe an ``open`` audit event."""
    path = _as_path(_arg(args, 0))
    if path is None:
        return None
    mode_value = _arg(args, 1)
    mode = _normalize_mode(mode_value) if isinstance(mode_value, str) else "os.open"
    return EventKind.FILE_OPEN, redact_text(path), {"mode": mode, "relative": _relative(path)}


def _describe_listing(args: tuple[object, ...]) -> Described | None:
    """Describe ``os.listdir`` and ``os.scandir`` events."""
    raw = _arg(args, 0)
    path = "." if raw is None else _as_path(raw)
    if path is None:
        return None
    return EventKind.DIR_LIST, redact_text(path), {"relative": _relative(path)}


def _describe_path(kind: EventKind, args: tuple[object, ...]) -> Described | None:
    """Describe a single-path event such as mkdir, remove or chdir."""
    path = _as_path(_arg(args, 0))
    if path is None:
        return None
    return kind, redact_text(path), {"relative": _relative(path)}


def _describe_rename(args: tuple[object, ...]) -> Described | None:
    """Describe an ``os.rename`` event."""
    source = _as_path(_arg(args, 0))
    destination = _as_path(_arg(args, 1))
    if source is None or destination is None:
        return None
    return (
        EventKind.FILE_RENAME,
        redact_text(source),
        {"destination": redact_text(destination), "relative": _relative(source)},
    )


def _describe_env_write(args: tuple[object, ...], action: str) -> Described | None:
    """Describe ``os.putenv`` or ``os.unsetenv``. The value is never read."""
    key = _as_path(_arg(args, 0))
    if key is None:
        return None
    return EventKind.ENV_WRITE, redact_text(key), {"action": action}


def _describe_dns(args: tuple[object, ...]) -> Described | None:
    """Describe a ``socket.getaddrinfo`` event."""
    host_arg = _arg(args, 0)
    port_arg = _arg(args, 1)
    host = "" if host_arg is None else _text(host_arg)
    target = host if port_arg is None else f"{host}:{_text(port_arg)}"
    return EventKind.DNS_LOOKUP, redact_text(target), {}


def _describe_connect(args: tuple[object, ...]) -> Described | None:
    """Describe a ``socket.connect`` event."""
    return EventKind.NETWORK_CONNECT, redact_text(format_address(_arg(args, 1))), {}


def _describe_popen(args: tuple[object, ...]) -> Described | None:
    """Describe a ``subprocess.Popen`` event."""
    executable = _arg(args, 0)
    argv = _argv_from(_arg(args, 1))
    env = _arg(args, 3)
    if not argv:
        return None
    program = argv[0] if executable is None else _text(executable)
    return _process_description(program, argv, env_override=env is not None, shell=False)


def _describe_spawn(args: tuple[object, ...]) -> Described | None:
    """Describe an ``os.spawn*`` event, using the path as the program."""
    program = _as_path(_arg(args, 1))
    argv = _argv_from(_arg(args, 2))
    env = _arg(args, 3)
    if program is None or not argv:
        return None
    return _process_description(program, argv, env_override=env is not None, shell=False)


def _describe_system(args: tuple[object, ...]) -> Described | None:
    """Describe an ``os.system`` event. The command text is never stored."""
    command = _arg(args, 0)
    if not isinstance(command, (str, bytes)):
        return None
    argv = _split_command(_text(command))
    if not argv:
        return None
    return _process_description(argv[0], argv, env_override=False, shell=True)  # noqa: S604


def _describe(event: str, args: tuple[object, ...]) -> Described | None:  # noqa: PLR0911, PLR0912
    """Translate an audit event into ``(kind, target, attributes)``.

    Returns None when the event should be ignored. Targets are redacted, and
    environment values, command arguments and request data are never included.
    """
    if event == "open":
        return _describe_open(args)
    if event in ("os.listdir", "os.scandir"):
        return _describe_listing(args)
    if event == "os.mkdir":
        return _describe_path(EventKind.DIR_CREATE, args)
    if event in ("os.remove", "os.rmdir"):
        return _describe_path(EventKind.FILE_REMOVE, args)
    if event == "os.rename":
        return _describe_rename(args)
    if event == "os.chdir":
        return _describe_path(EventKind.CHDIR, args)
    if event == "os.putenv":
        return _describe_env_write(args, "set")
    if event == "os.unsetenv":
        return _describe_env_write(args, "unset")
    if event == "socket.getaddrinfo":
        return _describe_dns(args)
    if event == "socket.connect":
        return _describe_connect(args)
    if event == "subprocess.Popen":
        return _describe_popen(args)
    if event == "os.system":
        return _describe_system(args)
    if event == "os.spawn":
        return _describe_spawn(args)
    return None


def _audit_hook(event: str, args: tuple[object, ...]) -> None:
    """Process-wide audit hook. Must never raise."""
    if event not in HANDLED_AUDIT_EVENTS:
        return
    observer = _ACTIVE
    if observer is None or _guard_busy():
        return
    _GUARD.busy = True
    try:
        frame = sys._getframe(1)
        if _from_import_system(frame):
            return
        described = _describe(event, args)
        if described is None:
            return
        kind, target, attributes = described
        observer._record(kind, target, attributes, frame)
    except Exception as exc:
        observer._add_error(f"{event}: {type(exc).__name__}: {exc}")
    finally:
        _GUARD.busy = False


def _make_env_getitem(original: Callable[..., str]) -> Callable[[object, str], str]:
    """Wrap ``os._Environ.__getitem__`` so that key reads are recorded."""

    def tracked_getitem(environ_obj: object, key: str) -> str:
        """Return the value for ``key`` and record the read by name only."""
        observer = _ACTIVE
        if observer is None or environ_obj is not os.environ or _guard_busy():
            return original(environ_obj, key)
        frame = sys._getframe(1)
        _GUARD.busy = True
        try:
            try:
                value = original(environ_obj, key)
            except KeyError:
                observer._record_guarded(
                    EventKind.ENV_READ, redact_text(str(key)), {"present": "false"}, frame
                )
                raise
            observer._record_guarded(
                EventKind.ENV_READ, redact_text(str(key)), {"present": "true"}, frame
            )
            return value
        finally:
            _GUARD.busy = False

    return tracked_getitem


class RuntimeObserver:
    """Record runtime events raised inside a ``with`` block for project code.

    Only one observer can be active at a time. Events are attributed to the
    nearest stack frame inside ``project_root`` that is not part of
    AssumptionRadar or of a virtual environment.
    """

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        *,
        max_events: int = 10_000,
        kinds: Iterable[EventKind] | None = None,
        track_env_reads: bool = False,
        require_project_frame: bool = True,
    ) -> None:
        """Create an observer.

        Args:
            project_root: Existing directory that project files must be inside.
            max_events: Maximum number of stored events before counting drops.
            kinds: Event kinds to record. None records every kind.
            track_env_reads: Also record ``os.environ`` reads (opt-in).
            require_project_frame: Drop events with no project frame when True.

        Raises:
            ValueError: If ``max_events`` is below 1 or ``project_root`` is not
                an existing directory.
        """
        if max_events < 1:
            raise ValueError(f"max_events must be >= 1, got {max_events}")
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise ValueError(f"project_root must be an existing directory, got {str(root)!r}")
        self._root_display: str = os.fspath(root)
        self._root_key: str = os.path.normcase(os.path.abspath(self._root_display))  # noqa: PTH100
        self._max_events = max_events
        self._kinds: frozenset[EventKind] | None = None if kinds is None else frozenset(kinds)
        self._track_env_reads = track_env_reads
        self._require_project_frame = require_project_frame
        self._lock = threading.Lock()
        self._events: list[ObservedEvent] = []
        self._errors: list[str] = []
        self._dropped = 0
        self._unattributed = 0
        self._test_id: str | None = None
        self._file_cache: dict[str, bool] = {}
        self._env_original: Callable[..., str] | None = None

    def __enter__(self) -> Self:
        """Activate the observer.

        Raises:
            ObserverError: If another observer is already active.
        """
        global _ACTIVE, _HOOK_INSTALLED  # noqa: PLW0603
        with _ACTIVATION_LOCK:
            if _ACTIVE is not None:
                raise ObserverError("another RuntimeObserver is already active")
            if not _HOOK_INSTALLED:
                sys.addaudithook(_audit_hook)
                _HOOK_INSTALLED = True
            if self._track_env_reads:
                env_cls = type(os.environ)
                self._env_original = env_cls.__getitem__
                env_cls.__getitem__ = _make_env_getitem(self._env_original)  # type: ignore[method-assign]
            _ACTIVE = self
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Deactivate the observer. Always restores state and never suppresses."""
        global _ACTIVE  # noqa: PLW0603
        with _ACTIVATION_LOCK:
            if _ACTIVE is self:
                _ACTIVE = None
            if self._env_original is not None:
                type(os.environ).__getitem__ = self._env_original  # type: ignore[method-assign]
                self._env_original = None

    @property
    def active(self) -> bool:
        """True while this observer is the active one."""
        return _ACTIVE is self

    def set_test(self, test_id: str | None) -> None:
        """Attach ``test_id`` to events recorded from now on."""
        with self._lock:
            self._test_id = test_id

    def observation(self) -> Observation:
        """Return a snapshot of the recorded events. Usable after exit."""
        with self._lock:
            return Observation(
                events=tuple(self._events),
                dropped=self._dropped,
                unattributed=self._unattributed,
                errors=tuple(self._errors),
            )

    def _add_error(self, message: str) -> None:
        """Keep at most ``MAX_ERRORS`` handler error messages."""
        with self._lock:
            if len(self._errors) < MAX_ERRORS:
                self._errors.append(message)

    def _record_guarded(
        self,
        kind: EventKind,
        target: str,
        attributes: dict[str, str],
        frame: FrameType | None,
    ) -> None:
        """Record an event from a wrapper. Errors are contained, never raised."""
        try:
            if _from_import_system(frame):
                return
            self._record(kind, target, attributes, frame)
        except Exception as exc:
            self._add_error(f"{kind.value}: {type(exc).__name__}: {exc}")

    def _record(
        self,
        kind: EventKind,
        target: str,
        attributes: dict[str, str],
        frame: FrameType | None,
    ) -> None:
        """Filter, attribute and buffer one event."""
        if self._kinds is not None and kind not in self._kinds:
            return
        location = self._locate(frame)
        if location is None and self._require_project_frame:
            with self._lock:
                self._unattributed += 1
            return
        with self._lock:
            if len(self._events) >= self._max_events:
                self._dropped += 1
                return
            self._events.append(
                ObservedEvent(
                    kind=kind,
                    target=target,
                    attributes=attributes,
                    location=location,
                    test_id=self._test_id,
                )
            )

    def _locate(self, frame: FrameType | None) -> SourceLocation | None:
        """Return the location of the nearest project frame, if any."""
        current = frame
        while current is not None:
            filename = current.f_code.co_filename
            if self._is_project_file(filename):
                return SourceLocation.from_path(
                    filename,
                    self._root_display,
                    line=current.f_lineno,
                    symbol=current.f_code.co_qualname,
                )
            current = current.f_back
        return None

    def _is_project_file(self, filename: str) -> bool:
        """Return whether ``filename`` is project code, with a per-file cache."""
        cached = self._file_cache.get(filename)
        if cached is not None:
            return cached
        decision = self._compute_project_file(filename)
        self._file_cache[filename] = decision
        return decision

    def _compute_project_file(self, filename: str) -> bool:
        """Decide whether ``filename`` is project code outside AssumptionRadar and venvs."""
        if filename.startswith("<") and filename.endswith(">"):
            # Synthetic names such as "<frozen ...>" or "<string>" are not files.
            return False
        try:
            path = os.path.normcase(os.path.abspath(filename))  # noqa: PTH100
        except (TypeError, ValueError):
            return False
        if not _is_within(path, self._root_key):
            return False
        if _is_within(path, _PACKAGE_DIR):
            return False
        relative = os.path.relpath(path, self._root_key)
        parts = set(re.split(r"[\\/]", relative))
        return not (parts & _EXCLUDED_PARTS)
