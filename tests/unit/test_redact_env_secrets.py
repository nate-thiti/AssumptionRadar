"""Tests for masking secret environment values and for linear-time redaction (P1-16)."""

from __future__ import annotations

import time

import pytest

from assumptionradar.utils.redact import REDACTED, mask_values, redact_text, secret_env_values

LONG_VALUE = "v4lue-" + "0123456789"


@pytest.mark.parametrize(
    "name",
    [
        "API_TOKEN",
        "GITHUB_TOKEN",
        "DB_PASSWORD",
        "MYSQL_PASSWD",
        "CLIENT_SECRET",
        "AWS_SECRET_ACCESS_KEY",
        "OPENAI_API_KEY",
        "STRIPE_APIKEY",
        "ACCESS_KEY",
        "PRIVATE_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "STRIPE_KEY",
        "KEY",
        "api_token",  # case-insensitive
    ],
)
def test_secret_names(name: str) -> None:
    assert secret_env_values({name: LONG_VALUE}) == frozenset({LONG_VALUE})


@pytest.mark.parametrize(
    "name",
    ["PATH", "HOME", "PWD", "KEYBOARD_LAYOUT", "MONKEY", "GIT_AUTHOR_NAME", "TOKENIZERS"],
)
def test_ordinary_names_are_not_secret(name: str) -> None:
    # "TOKENIZERS" contains "TOKEN": accepted over-redaction would be fine, but
    # the rule is word-based, so it is not a secret name.
    assert secret_env_values({name: LONG_VALUE}) == frozenset()


def test_short_values_are_ignored() -> None:
    # Short values such as "true" or "1234567" would mask ordinary words in output.
    assert secret_env_values({"API_TOKEN": "true", "DB_PASSWORD": "1234567"}) == frozenset()
    assert secret_env_values({"DB_PASSWORD": "12345678"}) == frozenset({"12345678"})


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AR_TEST_API_TOKEN", LONG_VALUE)
    assert LONG_VALUE in secret_env_values()


def test_mask_values_longest_first() -> None:
    assert mask_values("abc-secret-long and secret-l", ["secret-l", "abc-secret-long"]) == (
        f"{REDACTED} and {REDACTED}"
    )
    assert mask_values("nothing", []) == "nothing"
    assert mask_values("ab abc", ["ab"]) == "ab abc"  # shorter than 4: ignored


def test_redact_text_extra_secrets_still_work() -> None:
    assert redact_text("x=" + LONG_VALUE, home=None, extra_secrets=[LONG_VALUE]) == f"x={REDACTED}"


# ------------------------------------------------------------ performance


@pytest.mark.parametrize(
    "text",
    [
        "x" * 1_000_000,
        "token" * 200_000,
        "a1.-" * 250_000,
        "https" * 200_000,
        "Bearer " * 100_000,
    ],
    ids=["letters", "keyword-run", "name-chars", "scheme-run", "bearer-run"],
)
def test_redaction_is_linear_on_large_output(text: str) -> None:
    start = time.perf_counter()
    redact_text(text, home=None, max_length=len(text))
    assert time.perf_counter() - start < 5.0


def test_assignment_and_url_still_redacted_after_regex_change() -> None:
    assert redact_text("my_db_password=abc123", home=None) == f"my_db_password={REDACTED}"
    assert redact_text('"api_key": "abc123"', home=None) == f'"api_key": "{REDACTED}"'
    assert redact_text("git+https://u:p4ss@host/x", home=None) == f"git+https://u:{REDACTED}@host/x"
