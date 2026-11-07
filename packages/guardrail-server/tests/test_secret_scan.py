"""The secret scan (`guardrail_server.secret_scan`, R7-14): vendor token shapes, key assignments in
any case and in JSON, and text normalized (NFKC, no zero-width or format characters) before the
match. Each value is built by concatenation, so no secret literal sits in the file.
"""

from __future__ import annotations

import re
import time

import pytest
from guardrail_server.secret_scan import find_secret, normalize

ALNUM_36 = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # pragma: allowlist secret
ALNUM_20 = "a1b2c3d4e5f6a7b8c9d0"  # pragma: allowlist secret
ZWSP = "\u200b"
JWT_HEAD = "eyJ" + "hbGciOiJIUzI1NiJ9"  # pragma: allowlist secret
JWT_BODY = "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0"  # pragma: allowlist secret
JWT = JWT_HEAD + "." + JWT_BODY + "." + "SflKxwRJSMeKKF2QT4fw"  # pragma: allowlist secret
GOOGLE_KEY = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"  # pragma: allowlist secret
DB_URL = "postgres://app:" + "s3cretpass@db.internal:5432/x"  # pragma: allowlist secret
FAST_S = 0.5
"""suggested: the budget on a quiet machine."""
SLOWDOWN = 200
"""suggested: how many times one plain linear scan of 1 MB the whole check may take. The check
runs about 20 linear patterns (15 to 35 times the scan, measured under load); a backtracking one
takes hours on 1 MB, far past this. The budget is loose on purpose: it catches backtracking, not a
linear slowdown of 10 times or less."""
BASELINE_TEXT = "a." * 500_000
"""1 MB that the plain scan reads to the end: it measures how fast this machine is right now."""


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("github_token", "ghp_" + ALNUM_36),  # pragma: allowlist secret
        ("github_token", "gho_" + ALNUM_36),  # pragma: allowlist secret
        ("github_token", "ghs_" + ALNUM_36 + "XY"),  # pragma: allowlist secret
        ("github_token", "github_pat_" + "11ABCDEFG0" + ALNUM_36),  # pragma: allowlist secret
        ("slack_token", "xoxb-" + "1234567890-abcdefABCDEF"),  # pragma: allowlist secret
        ("slack_token", "xoxp-" + "9876543210-zyxwvu"),  # pragma: allowlist secret
        ("jwt", JWT),
        ("aws_access_key", "AKIA" + "Q3EXAMPLE7KEY2ZZ"),  # pragma: allowlist secret
        ("aws_access_key", "ASIA" + "Q3EXAMPLE7KEY2ZZ"),  # pragma: allowlist secret
        ("gitlab_token", "glpat-" + ALNUM_20),  # pragma: allowlist secret
        ("google_api_key", GOOGLE_KEY),
        ("url_credentials", DB_URL),
        ("url_credentials", "HTTPS://bob:" + "pw@example.com"),  # pragma: allowlist secret
        ("key_assignment", "password=" + "hunter2hunter2"),  # pragma: allowlist secret
        ("key_assignment", "Password: " + "hunter2hunter2"),  # pragma: allowlist secret
        ("key_assignment", '"api_key": "' + 'abcdefgh1234"'),  # pragma: allowlist secret
        ("key_assignment", "'apikey':'" + "abcdefgh1234'"),  # pragma: allowlist secret
        ("key_assignment", "AWS_SECRET_ACCESS_KEY=" + "wJalrXUtnFEMI"),  # pragma: allowlist secret
        ("key_assignment", "db_passwd = " + "'zz99yy88xx77'"),  # pragma: allowlist secret
        ("key_assignment", "client_secret: " + "abcdefgh12"),  # pragma: allowlist secret
        ("key_assignment", "github-token=" + "abcdefgh12"),  # pragma: allowlist secret
        ("key_assignment", "API_TOKEN=" + "abcdefgh12345"),  # pragma: allowlist secret
        ("bearer", "bearer " + "abcDEF123456ghiJKL7890xyz"),  # pragma: allowlist secret
        ("bearer", "BEARER " + "abcDEF123456ghiJKL7890xyz"),  # pragma: allowlist secret
        ("bearer", "Bearer: " + "abcDEF123456ghiJKL7890xyz"),  # pragma: allowlist secret
        ("github_token", "ghu_" + ALNUM_20),  # pragma: allowlist secret (20, the redact width)
        ("api_key", "key_sk-" + ALNUM_20),  # pragma: allowlist secret (an underscore before)
        ("jwt", JWT_HEAD + ".ab.cd"),  # pragma: allowlist secret (the redact JWT shape)
        ("key_assignment", "PWD=" + "hunter2hunter2"),  # pragma: allowlist secret
        ("key_assignment", "x-api-key: " + "abcdefgh12"),  # pragma: allowlist secret
    ],
)
def test_find_secret_returns_the_kind_when_a_shape_is_in_the_text(kind: str, value: str) -> None:
    """Each shape is found inside prose and named by its kind."""
    assert find_secret(f"the tool said: {value} (end)") == kind


@pytest.mark.parametrize(
    "text",
    [
        "sk" + ZWSP + "-" + ALNUM_20,  # pragma: allowlist secret
        "sk-" + ALNUM_20[:10] + "\u00ad" + ALNUM_20[10:],  # pragma: allowlist secret
        "s\u2060k\ufeff-\u200c" + ALNUM_20,  # pragma: allowlist secret
        "\uff53\uff4b\uff0d" + ALNUM_20,  # pragma: allowlist secret (full-width `sk-`)
    ],
)
def test_find_secret_returns_the_kind_when_a_key_is_split_by_hidden_characters(text: str) -> None:
    """A key split by zero-width or format characters, or in full-width letters, is still found."""
    assert find_secret(text) == "api_key"


def test_normalize_drops_format_characters_after_nfkc() -> None:
    """NFKC first, then every zero-width and format character is gone."""
    assert normalize("a\u200bb\u200cc\u200dd\u2060e\ufefff\u00adg\u202eh\uff21") == "abcdefghA"


@pytest.mark.parametrize(
    "text",
    [
        "max_tokens: 1000",
        '"max_tokens": 100000000',
        "the token is valid",
        "Your token: please",
        "TOKEN_COUNT = 12345",
        "token_limit=10000000",
        "MAX_TOKEN = 1234567",
        "PASSWORD_RESET_URL is documented",
        "password: <REDACTED_VALUE>",
        "API_KEY=${OPENAI_API_KEY_HERE}",
        "password = ************",
        "send the Bearer token in a header",
        "Bearer authentication is common.",
        "password=abc&user=jane",
        "token:\nabcdefghijkl",
        "xoxo-abcdefghijklmn",
        "ghp_short",
        "xoxb-",
        "eyJhbGciOi is how a JWT starts",
        "glpat-short",
        "AIzaShort",
        "https://example.com/path?q=1",
        "https://user@example.com",
        "mailto:jane:doe@example.com",
    ],
)
def test_find_secret_returns_none_when_a_text_only_looks_like_a_secret(text: str) -> None:
    """A text near a secret shape, but not one, passes."""
    assert find_secret(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "a_" * 500_000,
        "a_" * 500_000 + "token",  # past the `key_assignment` prefilter
        "key " + "a_" * 500_000,
        "a." * 500_000,
        "x://u:" + ":" * 1_000_000,
        "token" + " " * 1_000_000,
        "eyJ" + "a" * 1_000_000,
    ],
)
def test_find_secret_runs_in_linear_time_when_the_text_is_hostile(text: str) -> None:
    """A 1 MB run built to make a pattern backtrack is scanned quickly."""
    plain = re.compile(r"[A-Za-z0-9]{20,}")
    start = time.perf_counter()
    plain.search(BASELINE_TEXT)  # never matches, so it scans all of it
    baseline = time.perf_counter() - start
    start = time.perf_counter()
    find_secret(text)
    elapsed = time.perf_counter() - start

    # Against a scan timed in the same run, so a busy machine does not fail it.
    assert elapsed < max(FAST_S, SLOWDOWN * baseline)
