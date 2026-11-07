# These tests call os-level APIs on purpose to raise audit events.
# ruff: noqa: PTH102, PTH104, PTH106, PTH107, PTH123, PTH208, S607, SIM105
"""Contract tests for assumptionradar.analysis.runtime (ADR-0002).

These tests exercise the real audit hook in this process. Events are attributed
to the first stack frame inside ``PROJECT_ROOT`` that is not part of
AssumptionRadar itself, so calls made directly in this file are attributed here.
"""

from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from assumptionradar.analysis import runtime
from assumptionradar.analysis.runtime import (
    HANDLED_AUDIT_EVENTS,
    EventKind,
    Observation,
    ObservedEvent,
    RuntimeObserver,
)
from assumptionradar.core.exceptions import AssumptionRadarError, ObserverError
from assumptionradar.core.models import SourceLocation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = "tests/unit/test_runtime.py"


def here() -> int:
    """Line number of the caller's next line."""
    return sys._getframe(1).f_lineno + 1


def kinds(obs: Observation) -> list[EventKind]:
    return [e.kind for e in obs.events]


def of_kind(obs: Observation, kind: EventKind) -> list[ObservedEvent]:
    return [e for e in obs.events if e.kind is kind]


# --------------------------------------------------------------------- basics


class TestBasics:
    def test_event_kinds(self) -> None:
        assert [k.value for k in EventKind] == [
            "file_open",
            "dir_list",
            "dir_create",
            "file_remove",
            "file_rename",
            "chdir",
            "env_read",
            "env_write",
            "dns_lookup",
            "network_connect",
            "subprocess",
        ]

    def test_handled_audit_events(self) -> None:
        assert {
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
        } == set(HANDLED_AUDIT_EVENTS)

    def test_observer_error_type(self) -> None:
        assert issubclass(ObserverError, AssumptionRadarError)

    def test_invalid_arguments(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            RuntimeObserver(PROJECT_ROOT, max_events=0)
        with pytest.raises(ValueError):
            RuntimeObserver(tmp_path / "missing")

    def test_nothing_recorded_outside_block(self, tmp_path: Path) -> None:
        obs = RuntimeObserver(PROJECT_ROOT)
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        with obs:
            pass
        (tmp_path / "a.txt").read_text(encoding="utf-8")
        assert obs.observation().events == ()

    def test_active_flag(self) -> None:
        obs = RuntimeObserver(PROJECT_ROOT)
        assert not obs.active
        with obs:
            assert obs.active
        assert not obs.active

    def test_nested_activation_rejected(self) -> None:
        with (
            RuntimeObserver(PROJECT_ROOT),
            pytest.raises(ObserverError),
            RuntimeObserver(PROJECT_ROOT),
        ):
            pass

    def test_reusable_sequentially(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        first = RuntimeObserver(PROJECT_ROOT)
        with first:
            f.read_text(encoding="utf-8")
        second = RuntimeObserver(PROJECT_ROOT)
        with second:
            f.read_text(encoding="utf-8")
        assert len(of_kind(first.observation(), EventKind.FILE_OPEN)) == 1
        assert len(of_kind(second.observation(), EventKind.FILE_OPEN)) == 1


# --------------------------------------------------------------- filesystem


class TestFilesystem:
    def test_relative_open_with_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        Path("data.txt").write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            line = here()
            with open("data.txt", encoding="utf-8") as fh:
                fh.read()
        events = of_kind(obs.observation(), EventKind.FILE_OPEN)
        assert len(events) == 1
        ev = events[0]
        assert ev.target == "data.txt"
        assert dict(ev.attributes) == {"mode": "r", "relative": "true"}
        assert ev.location == SourceLocation(
            THIS_FILE, line=line, symbol="TestFilesystem.test_relative_open_with_location"
        )
        assert ev.test_id is None

    def test_absolute_open_is_marked_and_redacted(self, tmp_path: Path) -> None:
        f = tmp_path / "abs.txt"
        f.write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            f.read_text(encoding="utf-8")
        ev = of_kind(obs.observation(), EventKind.FILE_OPEN)[0]
        assert ev.attributes["relative"] == "false"
        assert ev.target.endswith("abs.txt")
        if str(tmp_path).lower().startswith(str(Path.home()).lower()):
            assert ev.target.startswith("~")
            assert str(Path.home()) not in ev.target

    def test_write_mode(self, tmp_path: Path) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs:
            (tmp_path / "w.txt").write_text("x", encoding="utf-8")
        assert of_kind(obs.observation(), EventKind.FILE_OPEN)[0].attributes["mode"] == "w"

    def test_file_descriptor_open_is_ignored(self, tmp_path: Path) -> None:
        fd = os.open(tmp_path / "fd.txt", os.O_CREAT | os.O_WRONLY)
        try:
            with RuntimeObserver(PROJECT_ROOT) as obs, open(fd, "w", closefd=False) as fh:
                fh.write("x")
        finally:
            os.close(fd)
        assert of_kind(obs.observation(), EventKind.FILE_OPEN) == []

    def test_os_open_has_no_mode(self, tmp_path: Path) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs:
            fd = os.open(tmp_path / "o.txt", os.O_CREAT | os.O_WRONLY)
        os.close(fd)
        assert of_kind(obs.observation(), EventKind.FILE_OPEN)[0].attributes["mode"] == "os.open"

    def test_imports_are_not_reported_as_file_reads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "ar_fresh_module_xyz.py").write_text("VALUE = 1\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        with RuntimeObserver(PROJECT_ROOT) as obs:
            mod = importlib.import_module("ar_fresh_module_xyz")
        assert mod.VALUE == 1
        assert not [e for e in obs.observation().events if "ar_fresh_module_xyz" in e.target]

    def test_directory_operations(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        Path("f.txt").write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            os.listdir(".")
            list(os.scandir("."))
            os.mkdir("sub")
            os.rename("f.txt", "g.txt")
            os.remove("g.txt")
            os.rmdir("sub")
            os.chdir(tmp_path)
        assert kinds(obs.observation()) == [
            EventKind.DIR_LIST,
            EventKind.DIR_LIST,
            EventKind.DIR_CREATE,
            EventKind.FILE_RENAME,
            EventKind.FILE_REMOVE,
            EventKind.FILE_REMOVE,
            EventKind.CHDIR,
        ]
        rename = of_kind(obs.observation(), EventKind.FILE_RENAME)[0]
        assert rename.target == "f.txt"
        assert rename.attributes["destination"] == "g.txt"
        assert of_kind(obs.observation(), EventKind.DIR_CREATE)[0].attributes["relative"] == "true"


# -------------------------------------------------------------- environment


class TestEnvironment:
    def test_env_write_records_name_never_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = "s3cr3t-" + "value-12345"
        with RuntimeObserver(PROJECT_ROOT) as obs:
            monkeypatch.setenv("AR_TEST_SECRET", secret)
            monkeypatch.delenv("AR_TEST_SECRET")
        events = of_kind(obs.observation(), EventKind.ENV_WRITE)
        assert [e.target for e in events] == ["AR_TEST_SECRET", "AR_TEST_SECRET"]
        assert [e.attributes["action"] for e in events] == ["set", "unset"]
        assert secret not in repr(obs.observation().to_dict())

    def test_env_reads_not_tracked_by_default(self) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs:
            os.environ.get("PATH")
        assert of_kind(obs.observation(), EventKind.ENV_READ) == []

    def test_env_reads_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AR_PRESENT", "secret-present-value")
        monkeypatch.delenv("AR_MISSING", raising=False)
        with RuntimeObserver(PROJECT_ROOT, track_env_reads=True) as obs:
            os.environ.get("AR_MISSING")
            os.getenv("AR_PRESENT")
            _ = "AR_MISSING" in os.environ
            try:
                os.environ["AR_MISSING"]
            except KeyError:
                pass
        events = of_kind(obs.observation(), EventKind.ENV_READ)
        assert [(e.target, e.attributes["present"]) for e in events] == [
            ("AR_MISSING", "false"),
            ("AR_PRESENT", "true"),
            ("AR_MISSING", "false"),
            ("AR_MISSING", "false"),
        ]
        assert all(e.location is not None and e.location.path == THIS_FILE for e in events)
        assert "secret-present-value" not in repr(obs.observation().to_dict())

    def test_env_read_tracking_is_fully_restored(self) -> None:
        original = type(os.environ).__getitem__
        environ_obj = os.environ
        with RuntimeObserver(PROJECT_ROOT, track_env_reads=True):
            pass
        assert type(os.environ).__getitem__ is original
        assert os.environ is environ_obj

    def test_restored_after_exception(self, tmp_path: Path) -> None:
        original = type(os.environ).__getitem__
        obs = RuntimeObserver(PROJECT_ROOT, track_env_reads=True)
        with pytest.raises(ZeroDivisionError), obs:
            _ = 1 / 0
        assert type(os.environ).__getitem__ is original
        assert not obs.active
        (tmp_path / "after.txt").write_text("x", encoding="utf-8")
        assert obs.observation().events == ()


# ------------------------------------------------------------------ network


class TestNetwork:
    def test_dns_lookup(self) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs:
            socket.getaddrinfo("localhost", 80)
        ev = of_kind(obs.observation(), EventKind.DNS_LOOKUP)[0]
        assert ev.target == "localhost:80"

    def test_dns_lookup_without_port(self) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs:
            socket.getaddrinfo("localhost", None)
        assert of_kind(obs.observation(), EventKind.DNS_LOOKUP)[0].target == "localhost"

    def test_connect_local_server(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            with (
                RuntimeObserver(PROJECT_ROOT) as obs,
                socket.create_connection(("127.0.0.1", port), timeout=5),
            ):
                pass
        finally:
            server.close()
        targets = [e.target for e in of_kind(obs.observation(), EventKind.NETWORK_CONNECT)]
        assert f"127.0.0.1:{port}" in targets

    def test_ipv6_formatting(self) -> None:
        assert runtime.format_address(("::1", 8080, 0, 0)) == "[::1]:8080"
        assert runtime.format_address(("10.0.0.1", 53)) == "10.0.0.1:53"
        assert runtime.format_address("/var/run/app.sock") == "/var/run/app.sock"


# --------------------------------------------------------------- subprocess


class TestSubprocess:
    def test_popen_records_program_not_arguments(self) -> None:
        secret_arg = "--token=" + "abcd1234secretvalue"
        with RuntimeObserver(PROJECT_ROOT) as obs:
            subprocess.run([sys.executable, "-c", "pass", secret_arg], check=True)
        ev = of_kind(obs.observation(), EventKind.SUBPROCESS)[0]
        assert Path(sys.executable).name in ev.target
        assert ev.attributes["path_lookup"] == "false"
        assert ev.attributes["argc"] == "4"
        assert ev.attributes["shell"] == "false"
        assert "abcd1234secretvalue" not in repr(obs.observation().to_dict())

    def test_path_lookup_detected_even_if_missing(self) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs, pytest.raises(OSError):
            subprocess.run(["ar-definitely-missing-tool-xyz", "--version"], check=False)
        ev = of_kind(obs.observation(), EventKind.SUBPROCESS)[0]
        assert ev.target == "ar-definitely-missing-tool-xyz"
        assert ev.attributes["path_lookup"] == "true"

    def test_env_override_flag_without_values(self) -> None:
        env = {**os.environ, "AR_CHILD_SECRET": "child-" + "secret-999"}
        with RuntimeObserver(PROJECT_ROOT) as obs:
            subprocess.run([sys.executable, "-c", "pass"], env=env, check=True)
        ev = of_kind(obs.observation(), EventKind.SUBPROCESS)[0]
        assert ev.attributes["env_override"] == "true"
        assert "secret-999" not in repr(obs.observation().to_dict())

    def test_os_system(self) -> None:
        with RuntimeObserver(PROJECT_ROOT) as obs:
            os.system("exit 0")  # noqa: S605
        ev = of_kind(obs.observation(), EventKind.SUBPROCESS)[0]
        assert ev.target == "exit"
        assert ev.attributes["shell"] == "true"


# ------------------------------------------------------ bounds and robustness


class TestBounds:
    def test_event_buffer_is_bounded(self, tmp_path: Path) -> None:
        f = tmp_path / "b.txt"
        f.write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT, max_events=3) as obs:
            for _ in range(5):
                f.read_text(encoding="utf-8")
        result = obs.observation()
        assert len(result.events) == 3
        assert result.dropped == 2

    def test_kind_filter(self, tmp_path: Path) -> None:
        (tmp_path / "k.txt").write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT, kinds={EventKind.DIR_LIST}) as obs:
            os.listdir(tmp_path)
            (tmp_path / "k.txt").read_text(encoding="utf-8")
        assert kinds(obs.observation()) == [EventKind.DIR_LIST]

    def test_unattributed_events_are_counted_not_stored(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (tmp_path / "u.txt").write_text("x", encoding="utf-8")
        with RuntimeObserver(elsewhere) as obs:
            (tmp_path / "u.txt").read_text(encoding="utf-8")
        result = obs.observation()
        assert result.events == ()
        assert result.unattributed >= 1

    def test_unattributed_events_kept_when_requested(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (tmp_path / "u.txt").write_text("x", encoding="utf-8")
        with RuntimeObserver(elsewhere, require_project_frame=False) as obs:
            (tmp_path / "u.txt").read_text(encoding="utf-8")
        ev = of_kind(obs.observation(), EventKind.FILE_OPEN)[0]
        assert ev.location is None

    def test_handler_errors_are_contained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(event: str, args: tuple[object, ...]) -> None:
            raise RuntimeError("boom in handler")

        monkeypatch.setattr(runtime, "_describe", boom)
        (tmp_path / "e.txt").write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            assert (tmp_path / "e.txt").read_text(encoding="utf-8") == "x"
        result = obs.observation()
        assert result.events == ()
        assert any("boom in handler" in err for err in result.errors)

    def test_error_list_is_bounded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(event: str, args: tuple[object, ...]) -> None:
            raise RuntimeError("x")

        monkeypatch.setattr(runtime, "_describe", boom)
        f = tmp_path / "e.txt"
        f.write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            for _ in range(50):
                f.read_text(encoding="utf-8")
        assert len(obs.observation().errors) <= runtime.MAX_ERRORS


# ---------------------------------------------------- attribution and threads


class TestAttribution:
    def test_set_test(self, tmp_path: Path) -> None:
        f = tmp_path / "t.txt"
        f.write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            f.read_text(encoding="utf-8")
            obs.set_test("tests/test_a.py::test_one")
            f.read_text(encoding="utf-8")
            obs.set_test(None)
            f.read_text(encoding="utf-8")
        assert [e.test_id for e in obs.observation().events] == [
            None,
            "tests/test_a.py::test_one",
            None,
        ]

    def test_events_from_other_threads_are_recorded(self, tmp_path: Path) -> None:
        f = tmp_path / "thread.txt"
        f.write_text("x", encoding="utf-8")

        def work() -> None:
            f.read_text(encoding="utf-8")

        with RuntimeObserver(PROJECT_ROOT) as obs:
            t = threading.Thread(target=work)
            t.start()
            t.join()
        assert len(of_kind(obs.observation(), EventKind.FILE_OPEN)) == 1

    def test_attributed_to_nearest_project_frame(self, tmp_path: Path) -> None:
        # pathlib (stdlib) performs the open; the event points at this file.
        f = tmp_path / "n.txt"
        f.write_text("x", encoding="utf-8")
        with RuntimeObserver(PROJECT_ROOT) as obs:
            line = here()
            f.read_text(encoding="utf-8")
        loc = of_kind(obs.observation(), EventKind.FILE_OPEN)[0].location
        assert loc is not None
        assert (loc.path, loc.line) == (THIS_FILE, line)


# ------------------------------------------------------------- serialisation


class TestSerialisation:
    def test_to_dict(self) -> None:
        ev = ObservedEvent(
            kind=EventKind.FILE_OPEN,
            target="data.txt",
            attributes={"relative": "true", "mode": "r"},
            location=SourceLocation("a.py", 3, symbol="f"),
            test_id="t::x",
        )
        assert ev.to_dict() == {
            "kind": "file_open",
            "target": "data.txt",
            "attributes": {"mode": "r", "relative": "true"},
            "location": {"path": "a.py", "line": 3, "column": None, "symbol": "f"},
            "test_id": "t::x",
        }
        obs = Observation(events=(ev,), dropped=1, unattributed=2, errors=("e",))
        assert obs.to_dict() == {
            "events": [ev.to_dict()],
            "dropped": 1,
            "unattributed": 2,
            "errors": ["e"],
        }

    def test_attributes_read_only(self) -> None:
        ev = ObservedEvent(EventKind.CHDIR, "x", {"relative": "true"}, None, None)
        with pytest.raises(TypeError):
            ev.attributes["a"] = "b"  # type: ignore[index]


class TestModuleLevelCode:
    """Module-level code runs under import-system frames but is project code."""

    def test_module_level_read_is_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "settings.txt").write_text("x", encoding="utf-8")
        (tmp_path / "ar_modlevel_cfg.py").write_text(
            "from pathlib import Path\n"
            "CONFIG = (Path(__file__).parent / 'settings.txt').read_text(encoding='utf-8')\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        with RuntimeObserver(tmp_path) as obs:
            importlib.import_module("ar_modlevel_cfg")
        events = obs.observation().events
        reads = [e for e in events if e.target.endswith("settings.txt")]
        assert len(reads) == 1
        assert reads[0].location is not None
        assert reads[0].location.path == "ar_modlevel_cfg.py"
        assert reads[0].location.line == 2
        assert reads[0].location.symbol == "<module>"
        # The import system's own reads of the module source are still ignored.
        assert not [e for e in events if e.target.endswith("ar_modlevel_cfg.py")]
