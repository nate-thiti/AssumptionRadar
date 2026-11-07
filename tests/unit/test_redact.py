"""Contract tests for assumptionradar.utils.redact.

Security requirement: secret values must never reach a report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assumptionradar.utils.redact import REDACTED, redact_text

# Fake secrets, built by concatenation so secret scanners do not flag this file.
GITHUB_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GITHUB_FINE = "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuv"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
SLACK = "xoxb-" + "123456789012-abcdefABCDEF"
SK_KEY = "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz012345"
JWT = (
    "eyJhbGciOiJIUzI1NiJ9"
    + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAx\nabc123\n-----END RSA PRIVATE KEY-----"
)

HOME = "C:\\Users\\alice"


def r(text: str, **kw: object) -> str:
    kw.setdefault("home", HOME)
    return redact_text(text, **kw)  # type: ignore[arg-type]


class TestSecrets:
    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            (f"token is {GITHUB_PAT} ok", GITHUB_PAT),
            (f"pat={GITHUB_FINE}", GITHUB_FINE),
            (f"aws {AWS_KEY}", AWS_KEY),
            (f"slack: {SLACK}", SLACK),
            (f"key {SK_KEY}", SK_KEY),
            (f"jwt {JWT}", JWT),
            ("Authorization: Bearer abc.def-ghi_123456", "abc.def-ghi_123456"),
            ("PASSWORD=hunter2hunter2", "hunter2hunter2"),
            ("api_key: 'q9w8e7r6t5'", "q9w8e7r6t5"),
            ('"client_secret": "s3cr3tvalue"', "s3cr3tvalue"),
            ("DB_PASSWD=pa55word", "pa55word"),
            ("postgres://admin:SuperSecret1@db.local:5432/app", "SuperSecret1"),
            (PRIVATE_KEY, "MIIEowIBAAKCAQEAx"),
        ],
    )
    def test_secret_value_is_removed(self, text: str, secret: str) -> None:
        out = r(text)
        assert secret not in out
        assert REDACTED in out

    def test_assignment_keeps_key_name(self) -> None:
        assert r("PASSWORD=hunter2hunter2") == f"PASSWORD={REDACTED}"
        assert r("api_key: 'q9w8e7r6t5'") == f"api_key: '{REDACTED}'"

    def test_bearer_keeps_scheme(self) -> None:
        assert r("Authorization: Bearer abc.def-ghi_123456") == f"Authorization: Bearer {REDACTED}"

    def test_url_keeps_user_and_host(self) -> None:
        assert r("postgres://admin:SuperSecret1@db.local:5432/app") == (
            f"postgres://admin:{REDACTED}@db.local:5432/app"
        )

    def test_extra_secrets_are_masked_longest_first(self) -> None:
        out = r("value=abcd1234 and abcd1234XYZ", extra_secrets=["abcd1234", "abcd1234XYZ"])
        assert "abcd1234" not in out
        assert out.count(REDACTED) == 2

    def test_short_extra_secrets_are_ignored(self) -> None:
        # Masking 1-3 char strings would destroy normal text.
        assert r("a b c", extra_secrets=["a", "", "bc"]) == "a b c"

    def test_harmless_text_unchanged(self) -> None:
        text = "open('config.json') failed: FileNotFoundError at line 12; tokens=3"
        assert r(text) == text

    def test_env_var_names_are_kept(self) -> None:
        assert r("missing env var DATABASE_URL") == "missing env var DATABASE_URL"


class TestHomeDirectory:
    def test_windows_home_backslash(self) -> None:
        assert r("C:\\Users\\alice\\proj\\a.py") == "~\\proj\\a.py"

    def test_windows_home_forward_slash(self) -> None:
        assert r("C:/Users/alice/proj/a.py") == "~/proj/a.py"

    def test_case_insensitive(self) -> None:
        assert r("c:\\users\\ALICE\\x") == "~\\x"

    def test_home_alone(self) -> None:
        assert r("cwd is C:\\Users\\alice") == "cwd is ~"

    def test_does_not_touch_other_users_with_same_prefix(self) -> None:
        assert r("C:\\Users\\alicesmith\\x") == "C:\\Users\\alicesmith\\x"

    def test_posix_home(self) -> None:
        assert redact_text("/home/bob/src/x.py and /home/bobby/y", home="/home/bob") == (
            "~/src/x.py and /home/bobby/y"
        )

    def test_accepts_path_object(self) -> None:
        assert redact_text("/home/bob/x", home=Path("/home/bob")) == "~/x"

    def test_default_home_is_current_user(self) -> None:
        home = str(Path.home())
        assert redact_text(f"{home}{'/'}x") == "~/x"

    def test_home_none_disables(self) -> None:
        assert redact_text("/home/bob/x", home=None) == "/home/bob/x"


class TestTruncation:
    def test_long_text_is_truncated_with_note(self) -> None:
        out = r("x" * 50, max_length=10)
        assert out == "x" * 10 + "...[truncated 40 chars]"

    def test_exact_length_untouched(self) -> None:
        assert r("x" * 10, max_length=10) == "x" * 10

    def test_secret_straddling_cut_is_still_masked(self) -> None:
        text = "y" * 8 + GITHUB_PAT
        out = r(text, max_length=12)
        assert "ghp_" not in out or GITHUB_PAT[:12] not in out
        assert GITHUB_PAT[4:12] not in out

    def test_default_max_length(self) -> None:
        out = r("z" * 5000)
        assert out.startswith("z" * 2000 + "...[truncated 3000 chars]")

    def test_max_length_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            r("x", max_length=0)


def test_empty_string() -> None:
    assert r("") == ""


def test_private_key_without_end_marker_is_masked() -> None:
    # Output may be truncated before the END line; the key body must still go.
    out = r("log: -----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nmore")
    assert "MIIEvQ" not in out
    assert out == f"log: {REDACTED}"


def test_home_not_replaced_inside_longer_path() -> None:
    assert redact_text("/srv/home/bob/x and /home/bob/y", home="/home/bob") == (
        "/srv/home/bob/x and ~/y"
    )
