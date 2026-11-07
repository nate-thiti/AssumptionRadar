"""Integration tests: real child processes via SubprocessRunner + Verifier.

Each test writes a tiny throw-away project into ``tmp_path`` and verifies one
of its tests under real environment changes. These are the end-to-end proof
that verification reports regressions, non-regressions and unsupported
conditions correctly on this platform.
"""

from __future__ import annotations

import locale
import os
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from assumptionradar.analysis.runner import CHILD_CONFIG_ENV, SubprocessRunner
from assumptionradar.analysis.scenarios import (
    REFERENCE_TIMESTAMP,
    cwd_scenario,
    encoding_scenario,
    env_unset_scenario,
    locale_scenario,
    tmpdir_scenario,
    tz_scenario,
)
from assumptionradar.analysis.verification import RunOutcome, Scenario, Verifier
from assumptionradar.core.models import VerificationStatus as VS

HOST_OFFSET = time.localtime(REFERENCE_TIMESTAMP).tm_gmtoff
FAKE_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"


def make_project(root: Path, test_body: str, files: dict[str, bytes] | None = None) -> str:
    """Write a minimal pytest project and return the node id of ``test_it``."""
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text(textwrap.dedent(test_body), encoding="utf-8")
    for rel, data in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return "tests/test_fixture.py::test_it"


def verify(root: Path, node: str, scenarios: list[Scenario], **runner_kw: object) -> object:
    runner = SubprocessRunner(**runner_kw)  # type: ignore[arg-type]
    return Verifier(root, runner=runner).verify(node, scenarios)


# ----------------------------------------------------------------------- runner


class TestRunnerBasics:
    def test_baseline_pass(self, tmp_path: Path) -> None:
        node = make_project(tmp_path, "def test_it():\n    assert True\n")
        r = SubprocessRunner()(node, tmp_path, None)
        assert r.outcome is RunOutcome.PASSED
        assert r.exit_code == 0
        assert r.duration_s > 0
        assert r.probe is not None
        assert "utc_offset_seconds" in r.probe

    def test_baseline_fail(self, tmp_path: Path) -> None:
        node = make_project(tmp_path, "def test_it():\n    assert 1 == 2\n")
        r = SubprocessRunner()(node, tmp_path, None)
        assert r.outcome is RunOutcome.FAILED
        assert "assert 1 == 2" in r.output_tail

    def test_unknown_test_id(self, tmp_path: Path) -> None:
        make_project(tmp_path, "def test_it():\n    pass\n")
        r = SubprocessRunner()("tests/test_fixture.py::test_missing", tmp_path, None)
        assert r.outcome in {RunOutcome.NO_TESTS, RunOutcome.ERROR}

    def test_timeout(self, tmp_path: Path) -> None:
        node = make_project(tmp_path, "import time\n\ndef test_it():\n    time.sleep(60)\n")
        r = SubprocessRunner(timeout_s=3)(node, tmp_path, None)
        assert r.outcome is RunOutcome.TIMEOUT
        assert r.exit_code is None
        assert r.duration_s < 30

    def test_child_config_env_is_hidden_from_tests(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            f"import os\n\ndef test_it():\n    assert {CHILD_CONFIG_ENV!r} not in os.environ\n",
        )
        assert SubprocessRunner()(node, tmp_path, None).outcome is RunOutcome.PASSED

    def test_output_is_redacted_and_bounded(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            f"def test_it():\n    print('x' * 20000)\n    assert False, {FAKE_TOKEN!r}\n",
        )
        r = SubprocessRunner(max_output_chars=1500)(node, tmp_path, None)
        assert r.outcome is RunOutcome.FAILED
        assert FAKE_TOKEN not in r.output_tail
        assert len(r.output_tail) <= 1500 + 40

    def test_parent_state_untouched(self, tmp_path: Path) -> None:
        env_before = dict(os.environ)
        cwd_before = Path.cwd()
        node = make_project(
            tmp_path, "import os\n\ndef test_it():\n    os.environ['X_LEAK'] = '1'\n"
        )
        SubprocessRunner()(node, tmp_path, cwd_scenario())
        assert dict(os.environ) == env_before
        assert Path.cwd() == cwd_before

    def test_project_modules_importable_from_isolated_cwd(self, tmp_path: Path) -> None:
        # The cwd scenario must test file access, not import resolution.
        node = make_project(
            tmp_path,
            "from helper import VALUE\n\ndef test_it():\n    assert VALUE == 1\n",
            {"helper.py": b"VALUE = 1\n"},
        )
        res = verify(tmp_path, node, [cwd_scenario()])
        assert res.status is VS.NO_DIFFERENCE  # type: ignore[attr-defined]

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        node = make_project(tmp_path, "def test_it():\n    pass\n")
        before = set(Path(tempfile.gettempdir()).glob("assumptionradar-*"))
        SubprocessRunner()(node, tmp_path, tmpdir_scenario())
        after = set(Path(tempfile.gettempdir()).glob("assumptionradar-*"))
        assert after <= before


# ---------------------------------------------------------- end-to-end verdicts


class TestCwdVerdicts:
    def test_relative_path_is_regression(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "def test_it():\n    with open('data.txt') as f:\n        assert f.read() == 'ok'\n",
            {"data.txt": b"ok"},
        )
        res = verify(tmp_path, node, [cwd_scenario()])
        assert res.status is VS.REGRESSION  # type: ignore[attr-defined]
        assert "FileNotFoundError" in res.scenarios[0].run.output_tail  # type: ignore[attr-defined]

    def test_file_relative_path_is_no_difference(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "from pathlib import Path\n\n"
            "def test_it():\n"
            "    p = Path(__file__).parent.parent / 'data.txt'\n"
            "    assert p.read_text() == 'ok'\n",
            {"data.txt": b"ok"},
        )
        res = verify(tmp_path, node, [cwd_scenario()])
        assert res.status is VS.NO_DIFFERENCE  # type: ignore[attr-defined]

    def test_module_level_relative_read_is_collection_regression(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "CONFIG = open('data.txt').read()\n\ndef test_it():\n    assert CONFIG == 'ok'\n",
            {"data.txt": b"ok"},
        )
        res = verify(tmp_path, node, [cwd_scenario()])
        assert res.status is VS.REGRESSION  # type: ignore[attr-defined]
        assert "collect" in res.scenarios[0].reason  # type: ignore[attr-defined]


class TestTzVerdicts:
    def test_hardcoded_local_offset_is_regression(self, tmp_path: Path) -> None:
        zone = "+9" if HOST_OFFSET != 32400 else "-5"
        node = make_project(
            tmp_path,
            f"import time\n\ndef test_it():\n    assert time.localtime({REFERENCE_TIMESTAMP}).tm_gmtoff == {HOST_OFFSET}\n",
        )
        res = verify(tmp_path, node, [tz_scenario(zone)])
        assert res.status is VS.REGRESSION  # type: ignore[attr-defined]

    def test_utc_aware_code_is_no_difference(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "from datetime import datetime, timezone\n\n"
            "def test_it():\n"
            f"    d = datetime.fromtimestamp({REFERENCE_TIMESTAMP}, tz=timezone.utc)\n"
            "    assert d.hour == 22\n",
        )
        res = verify(tmp_path, node, [tz_scenario("+9"), tz_scenario("-5")])
        assert res.status is VS.NO_DIFFERENCE  # type: ignore[attr-defined]

    def test_condition_that_does_not_apply_is_unsupported(self, tmp_path: Path) -> None:
        # Spike 0001: an IANA name is silently ignored by the Windows CRT.
        node = make_project(tmp_path, "def test_it():\n    pass\n")
        iana = Scenario(
            name="tz:iana-tokyo",
            description="IANA Asia/Tokyo",
            env_set={"TZ": "Asia/Tokyo"},
            expect={"utc_offset_seconds": "32400"},
        )
        res = verify(tmp_path, node, [iana])
        if sys.platform == "win32":
            assert res.scenarios[0].status is VS.UNSUPPORTED  # type: ignore[attr-defined]
            assert "did not take effect" in res.scenarios[0].reason  # type: ignore[attr-defined]
        else:
            assert res.scenarios[0].status in {VS.NO_DIFFERENCE, VS.UNSUPPORTED}  # type: ignore[attr-defined]


class TestEnvVerdicts:
    def test_required_variable_is_regression(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "import os\n\ndef test_it():\n    assert os.environ['AR_FIXTURE_URL'].startswith('http')\n",
        )
        environ = {**os.environ, "AR_FIXTURE_URL": "http://example.invalid"}
        res = verify(
            tmp_path,
            node,
            [env_unset_scenario("AR_FIXTURE_URL", environ=environ)],
            base_env=environ,
        )
        assert res.status is VS.REGRESSION  # type: ignore[attr-defined]
        assert "example.invalid" not in repr(res.to_dict())  # type: ignore[attr-defined]

    def test_variable_with_default_is_no_difference(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "import os\n\ndef test_it():\n    assert os.environ.get('AR_FIXTURE_URL', 'http://d').startswith('http')\n",
        )
        environ = {**os.environ, "AR_FIXTURE_URL": "http://example.invalid"}
        res = verify(
            tmp_path,
            node,
            [env_unset_scenario("AR_FIXTURE_URL", environ=environ)],
            base_env=environ,
        )
        assert res.status is VS.NO_DIFFERENCE  # type: ignore[attr-defined]


class TestTmpdirVerdicts:
    def test_assumes_simple_temp_path_is_regression(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "import tempfile\n\ndef test_it():\n    assert ' ' not in tempfile.gettempdir()\n",
        )
        if " " in tempfile.gettempdir():
            pytest.skip("host temp path already contains a space")
        res = verify(tmp_path, node, [tmpdir_scenario()])
        assert res.status is VS.REGRESSION  # type: ignore[attr-defined]

    def test_tmp_path_usage_is_no_difference(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "def test_it(tmp_path):\n    (tmp_path / 'x.txt').write_text('ok')\n    assert (tmp_path / 'x.txt').read_text() == 'ok'\n",
        )
        res = verify(tmp_path, node, [tmpdir_scenario()])
        assert res.status is VS.NO_DIFFERENCE  # type: ignore[attr-defined]


def _host_encoded_sample() -> bytes | None:
    """Bytes of a non-ASCII char in the host encoding that differ from UTF-8."""
    enc = locale.getpreferredencoding(False)
    for ch in ("é", "ก", "ß", "Ж"):
        try:
            data = ch.encode(enc)
        except UnicodeEncodeError:
            continue
        if data != ch.encode("utf-8"):
            return data
    if enc.replace("-", "").lower() == "utf8":
        return "é".encode()
    return None


class TestEncodingVerdicts:
    def test_default_encoding_read_is_regression(self, tmp_path: Path) -> None:
        data = _host_encoded_sample()
        if data is None:
            pytest.skip("no suitable non-ASCII sample for this host encoding")
        expected = data.decode(locale.getpreferredencoding(False))
        node = make_project(
            tmp_path,
            "from pathlib import Path\n\n"
            "def test_it():\n"
            "    p = Path(__file__).parent.parent / 'text.txt'\n"
            f"    assert open(p).read() == {expected!r}\n",
            {"text.txt": data},
        )
        res = verify(tmp_path, node, [encoding_scenario()])
        assert res.scenarios[0].status in {VS.REGRESSION, VS.UNSUPPORTED}  # type: ignore[attr-defined]
        if sys.platform == "win32":
            assert res.status is VS.REGRESSION  # type: ignore[attr-defined]

    def test_explicit_encoding_is_no_difference(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "from pathlib import Path\n\n"
            "def test_it():\n"
            "    p = Path(__file__).parent.parent / 'text.txt'\n"
            "    assert p.read_text(encoding='utf-8') == 'caf\\u00e9'\n",
            {"text.txt": "café".encode()},
        )
        res = verify(tmp_path, node, [encoding_scenario()])
        assert res.scenarios[0].status in {VS.NO_DIFFERENCE, VS.UNSUPPORTED}  # type: ignore[attr-defined]
        if sys.platform == "win32":
            assert res.status is VS.NO_DIFFERENCE  # type: ignore[attr-defined]


class TestLocaleVerdicts:
    def test_locale_number_format_is_regression_or_unsupported(self, tmp_path: Path) -> None:
        node = make_project(
            tmp_path,
            "import locale\n\ndef test_it():\n    assert locale.format_string('%.1f', 1.5) == '1.5'\n",
        )
        res = verify(tmp_path, node, [locale_scenario("de_DE")])
        status = res.scenarios[0].status  # type: ignore[attr-defined]
        assert status in {VS.REGRESSION, VS.UNSUPPORTED}
        if status is VS.UNSUPPORTED:
            assert "locale_applied" in res.scenarios[0].reason  # type: ignore[attr-defined]


class TestBaselineVerdicts:
    def test_failing_baseline_is_inconclusive(self, tmp_path: Path) -> None:
        node = make_project(tmp_path, "def test_it():\n    assert False\n")
        res = verify(tmp_path, node, [cwd_scenario()])
        assert res.status is VS.INCONCLUSIVE  # type: ignore[attr-defined]

    def test_missing_test_is_error(self, tmp_path: Path) -> None:
        make_project(tmp_path, "def test_it():\n    pass\n")
        res = verify(tmp_path, "tests/test_fixture.py::nope", [cwd_scenario()])
        assert res.status is VS.ERROR  # type: ignore[attr-defined]
