"""PoC-7: `chassis.redaction` masks credential shapes as `<SECRET>` (review R1-07).

Every literal here is a made-up value shaped like a credential, never a real one.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from chassis import redaction
from chassis.redaction import PATTERNS, SECRET_ASSIGNMENT, SECRET_PREFIXES, redact
from redaction_timing import assert_linear

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"  # pragma: allowlist secret
    "Proc-Type: 4,ENCRYPTED\n"
    "MIIEowIBAAKCAQEAabcdefghijklmnop\n"  # pragma: allowlist secret
    "qrstuvwxyz0123456789+/==\n"  # pragma: allowlist secret
    "-----END RSA PRIVATE KEY-----"
)


def test_redact_masks_both_tokens_in_an_engine_error_message() -> None:
    """The spec: a Bearer token and an `sk-` key in an `Error.message` are both gone."""
    text = "upstream said Bearer abc.def-123 and sk-abcdefgh12345678"  # pragma: allowlist secret

    out = redact(text)

    assert "abc.def-123" not in out
    assert "sk-abcdefgh12345678" not in out  # pragma: allowlist secret
    assert out == "upstream said <SECRET> and <SECRET>"


@pytest.mark.parametrize(
    "secret",
    [
        "Bearer abc.def-123",
        "bearer: Abcdefgh12345",
        "BEARER eyJhbGciOiJIUzI1NiJ9",
        "sk-abcdefgh12345678",  # pragma: allowlist secret
        "sk-proj-abcdefgh1234",  # pragma: allowlist secret
        "sk-ant-api03-abcdefgh1234",  # pragma: allowlist secret
        "ghp_" + "a1" * 18,
        "gho_" + "b2" * 18,
        "ghu_" + "c3" * 18,
        "ghs_" + "d4" * 18,
        "ghr_" + "e5" * 18,
        "github_pat_11ABCDEFG0123456789_abcdefghijkl",  # pragma: allowlist secret
        "xoxb-123456789012-abcdefghij",  # pragma: allowlist secret
        "xoxp-123456789012-abcdefghij",  # pragma: allowlist secret
        "xoxa-2-abcdefghij",  # pragma: allowlist secret
        "xoxr-abcdefghij12",  # pragma: allowlist secret
        "xoxs-abcdefghij12",  # pragma: allowlist secret
        "glpat-abcdefghij0123456789",  # pragma: allowlist secret
        "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
        "ASIA" + "ABCDEFGH01234567",
        "AIza" + "B" * 35,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456",  # pragma: allowlist secret
        "-----BEGIN PRIVATE KEY-----",  # pragma: allowlist secret
        "-----BEGIN OPENSSH PRIVATE KEY-----",  # pragma: allowlist secret
    ],
    ids=[
        "bearer",
        "bearer-colon-lowercase",
        "bearer-uppercase",
        "openai",
        "openai-project",
        "anthropic",
        "github-ghp",
        "github-gho",
        "github-ghu",
        "github-ghs",
        "github-ghr",
        "github-pat",
        "slack-xoxb",
        "slack-xoxp",
        "slack-xoxa",
        "slack-xoxr",
        "slack-xoxs",
        "gitlab",
        "aws-akia",
        "aws-asia",
        "google",
        "jwt",
        "pem-header",
        "pem-openssh-header",
    ],
)
def test_redact_masks_a_secret_when_it_has_a_known_shape(secret: str) -> None:
    """Each known credential shape becomes `<SECRET>`, whole."""
    text = f"got {secret} back"

    out = redact(text)

    assert out == "got <SECRET> back"


def test_redact_masks_a_private_key_through_its_end_line() -> None:
    """A PEM block is masked from the BEGIN line through the matching END line."""
    text = f"key:\n{PEM}\ndone"

    out = redact(text)

    assert out == "key:\n<SECRET>\ndone"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("password=hunter2 ok", "password=<SECRET> ok"),
        ("PASSWD: s3cr3t", "PASSWD: <SECRET>"),
        ("pwd=abc", "pwd=<SECRET>"),
        (
            "client_secret = 'p@ss w0rd' end",  # pragma: allowlist secret
            "client_secret = '<SECRET>' end",
        ),
        ('{"api_key": "abc123"}', '{"api_key": "<SECRET>"}'),  # pragma: allowlist secret
        ("GITHUB_TOKEN=abc123", "GITHUB_TOKEN=<SECRET>"),
        ("url?token=abc&page=2", "url?token=<SECRET>&page=2"),
        ("token: Bearer abcdefgh123", "token: <SECRET>"),
        ("api_key=sk-abcdefgh12345678", "api_key=<SECRET>"),  # pragma: allowlist secret
    ],
    ids=[
        "password-equals",
        "passwd-colon-uppercase",
        "pwd",
        "quoted-with-space",
        "json",
        "env-var",
        "query-string",
        "bearer-value",
        "key-value",
    ],
)
def test_redact_masks_the_value_when_a_secret_is_assigned(text: str, expected: str) -> None:
    """A `password=`-style assignment keeps its key and loses its value."""
    out = redact(text)

    assert out == expected


@pytest.mark.parametrize(
    "text",
    [
        "task-abcdefgh123 done",
        "risk-assessment-2026 done",
        "the bearer of bad news",
        "max_tokens=100 input_tokens: 5",
        "password=; then nothing",
        'password="" is empty',
        "ASIAN cuisine",
        "eyJ alone",
        "skip-it",
        "the ghp_ prefix",
    ],
    ids=[
        "sk-inside-a-word",
        "sk-inside-a-slug",
        "bearer-in-prose",
        "token-counts",
        "empty-assignment",
        "empty-quoted-assignment",
        "asia-word",
        "jwt-prefix-only",
        "sk-word",
        "github-prefix-only",
    ],
)
def test_redact_leaves_ordinary_words_alone_when_they_only_look_like_a_secret(text: str) -> None:
    """A word that only starts like a key shape, or an empty assignment, is not a secret."""
    out = redact(text)

    assert out == text


def test_redact_runs_the_secret_pattern_first_so_a_token_is_not_partly_a_phone() -> None:
    """`SECRET` comes first in `PATTERNS`, so the digits of a key never become a phone."""
    text = "key sk-415-555-0100-1234 end"  # pragma: allowlist secret

    out = redact(text)

    assert PATTERNS[0][0] == "SECRET"
    assert out == "key <SECRET> end"


def test_the_secret_shapes_are_one_list_of_named_patterns_that_compile() -> None:
    """`SECRET_PREFIXES` is the one list of shapes; each compiles on its own, for hand copies."""
    kinds = [kind for kind, _ in SECRET_PREFIXES]

    for _, source in SECRET_PREFIXES:
        re.compile(source)

    assert len(kinds) == len(set(kinds))
    assert {"bearer", "openai", "github", "slack", "gitlab", "aws", "google", "jwt", "pem"} <= set(
        kinds
    )
    assert SECRET_ASSIGNMENT.groupindex.keys() >= {"value"}


@pytest.mark.parametrize(
    ("build", "count"),
    [
        (lambda n: "sk-" * n, 33_334),
        (lambda n: "Bearer " * n, 14_286),
        (lambda n: "bearer" + " " * n, 100_000),
        (lambda n: 'password="' * n, 10_000),
        (lambda n: "token=" + " " * n, 100_000),
        (lambda n: "-----BEGIN PRIVATE KEY-----" * n, 3_704),  # pragma: allowlist secret
        (lambda n: "-----BEGIN PRIVATE KEY-----" + "-" * n, 100_000),  # pragma: allowlist secret
        (lambda n: "eyJ" * n, 33_334),
        (lambda n: "eyJa." * n, 20_000),
        (lambda n: "AKIA" * n, 25_000),
        (lambda n: "xoxb-" * n, 20_000),
    ],
    ids=[
        "sk-run",
        "bearer-run",
        "bearer-then-spaces",
        "open-quoted-passwords",
        "token-then-spaces",
        "pem-headers-no-end",
        "pem-header-then-dashes",
        "jwt-prefix-run",
        "jwt-short-segments",
        "aws-prefix-run",
        "slack-prefix-run",
    ],
)
def test_redact_stays_linear_when_a_long_text_is_full_of_secret_prefixes(
    build: Callable[[int], str], count: int
) -> None:
    """A 100k-character run of secret prefixes is redacted in linear time (target: under
    100 ms, `redaction_timing.TARGET_SECONDS`)."""
    assert_linear(redact, build, count)


# --- review fixes on R1-07 ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"token": 12345}', '{"token": <SECRET>}'),
        ("(token=abc)", "(token=<SECRET>)"),
        ("[pwd=abc]", "[pwd=<SECRET>]"),
        ("<secret=abc>", "<secret=<SECRET>>"),
    ],
    ids=["json-brace", "parens", "brackets", "angle"],
)
def test_redact_keeps_the_closing_bracket_when_an_unquoted_value_ends_at_one(
    text: str, expected: str
) -> None:
    """An unquoted value stops before `)`, `]`, `}`, or `>`, so the bracket stays."""
    out = redact(text)

    assert out == expected


def test_redact_masks_all_of_a_quoted_value_when_it_is_longer_than_256_characters() -> None:
    """A long quoted value is masked whole; no tail is left behind."""
    value = "z" * 300
    text = f'password="{value}" end'

    out = redact(text)

    assert out == 'password="<SECRET>" end'


def test_redact_masks_a_private_key_through_an_end_line_with_another_label() -> None:
    """An END line closes the block even when its label differs from the BEGIN line."""
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"  # pragma: allowlist secret
        "MIIEowIBAAKCAQEAabcdefghijklmnop\n"  # pragma: allowlist secret
        "-----END PRIVATE KEY-----\nafter"
    )

    out = redact(text)

    assert out == "<SECRET>\nafter"


def test_redact_masks_the_base64_lines_after_a_private_key_header_with_no_end_line() -> None:
    """With no END line, the header and the base64 lines after it are masked; prose stays."""
    text = (
        "-----BEGIN PRIVATE KEY-----\n"  # pragma: allowlist secret
        "MIIEowIBAAKCAQEAabcdefghijklmnop\n"  # pragma: allowlist secret
        "qrstuvwxyz0123456789+/==\n"  # pragma: allowlist secret
        "then the log went on"
    )

    out = redact(text)

    assert out == "<SECRET>\nthen the log went on"


@pytest.mark.parametrize(
    "text",
    [
        "missing Bearer authentication header",
        "Bearer credentials expected",
        "the secret is out",
        "a token bucket of 10",
        "max_tokens=100 prompt_tokens: 5",
        '"token_type": "bearer"',
        '"token_usage": {"total": 5}',
        "basic functionality works",
        "http://host:8080/path",
    ],
    ids=[
        "bearer-authentication",
        "bearer-credentials",
        "secret-in-prose",
        "token-bucket",
        "token-counts",
        "oauth-token-type",
        "token-usage-object",
        "basic-in-prose",
        "url-with-port",
    ],
)
def test_redact_leaves_prose_alone_when_it_uses_secret_words(text: str) -> None:
    """Prose and fields that only name a token or a scheme are not secrets."""
    out = redact(text)

    assert out == text


def test_redact_masks_a_long_letters_only_bearer_token() -> None:
    """A Bearer token of 16 or more letters is masked even with no digit or symbol."""
    text = "Bearer abcdefghijklmnopqr end"

    out = redact(text)

    assert out == "<SECRET> end"


def test_every_secret_shape_starts_with_a_literal_the_pre_check_finds() -> None:
    """`_SECRET_START` gates the full alternation, so a shape it misses would never be masked."""
    samples = {
        "bearer": "Bearer abc.def-123",
        "basic": "Basic dXNlcjpwYXNzd29yZA==",
        "openai": "sk-abcdefgh12345678",
        "github": "ghp_" + "a1" * 12,
        "slack": "xoxb-1234567890-abc",
        "gitlab": "glpat-" + "a1" * 12,
        "aws": "AKIAABCDEFGHIJKLMNOP",  # pragma: allowlist secret
        "google": "AIza" + "a" * 35,
        "jwt": "eyJhbGciOi.eyJzdWIi.c2ln",
        "pem": "-----BEGIN PRIVATE KEY-----",  # pragma: allowlist secret
    }

    assert set(samples) == {kind for kind, _ in redaction.SECRET_PREFIXES}
    for kind, sample in samples.items():
        assert redaction._SECRET_START.search(sample), kind
        assert "<SECRET>" in redact(f"x {sample} y"), kind


def test_redact_masks_a_url_encoded_bearer_header() -> None:
    """The `OTEL_EXPORTER_OTLP_HEADERS` form encodes the space as `%20`."""
    text = "Authorization=Bearer%20abc.def-123456 end"  # pragma: allowlist secret

    out = redact(text)

    assert "abc.def-123456" not in out
    assert out.endswith(" end")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("secret_key=abc123", "secret_key=<SECRET>"),
        ("aws_secret_access_key = abc123", "aws_secret_access_key = <SECRET>"),
        ("LITELLM_MASTER_KEY=abc123", "LITELLM_MASTER_KEY=<SECRET>"),
        ("CONFIG_S3_SECRET_KEY=abc123", "CONFIG_S3_SECRET_KEY=<SECRET>"),
        ("access_key: abc123", "access_key: <SECRET>"),
        ("private-key=abc123", "private-key=<SECRET>"),
        ("masterkey=abc123", "masterkey=<SECRET>"),
        ("credentials=abc123", "credentials=<SECRET>"),
        ("credential: abc123", "credential: <SECRET>"),
    ],
    ids=[
        "secret-key",
        "aws-secret-access-key",
        "litellm-master-key",
        "config-s3-secret-key",
        "access-key",
        "private-key",
        "master-key",
        "credentials",
        "credential",
    ],
)
def test_redact_masks_the_value_when_a_longer_key_name_holds_a_secret_word(
    text: str, expected: str
) -> None:
    """A key that holds a secret word (`secret_key`, `LITELLM_MASTER_KEY`) loses its value."""
    out = redact(text)

    assert out == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "Authorization: <SECRET>"),
        ("basic YWxhZGRpbjpvcGVuc2VzYW1l ok", "<SECRET> ok"),
    ],
    ids=["basic-padded", "basic-lowercase"],
)
def test_redact_masks_basic_credentials(text: str, expected: str) -> None:
    """A `Basic` credential of 8 or more base64 characters becomes `<SECRET>`."""
    out = redact(text)

    assert out == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "postgres://app:hunter2@db:5432/x",  # pragma: allowlist secret
            "postgres://app:<SECRET>@db:5432/x",
        ),
        ("redis://:hunter2@cache:6379", "redis://:<SECRET>@cache:6379"),
        ("https://u:p@ss@host/path", "https://u:<SECRET>@host/path"),
    ],
    ids=["user-and-password", "password-only", "at-sign-in-password"],
)
def test_redact_masks_only_the_password_in_a_url(text: str, expected: str) -> None:
    """A URL's userinfo password is masked; the scheme, user, and host stay."""
    out = redact(text)

    assert out == expected


@pytest.mark.parametrize(
    ("build", "count"),
    [
        (lambda n: "secret-" * n, 14_286),
        (lambda n: "_password" * n, 11_112),
        (lambda n: "a://:" * n, 20_000),
        (lambda n: "postgres://" + "u" * n, 100_000),
        (lambda n: "Basic " * n, 16_667),
        (lambda n: "Bearer " + "a" * n, 100_000),
        (lambda n: "-----BEGIN PRIVATE KEY-----\n" + "A\n" * n, 50_000),  # pragma: allowlist secret
    ],
    ids=[
        "dashed-secret-keys",
        "underscored-password-keys",
        "many-schemes",
        "url-with-no-at",
        "basic-run",
        "bearer-long-letters",
        "pem-many-short-lines",
    ],
)
def test_redact_stays_linear_when_a_long_text_repeats_a_key_or_url_shape(
    build: Callable[[int], str], count: int
) -> None:
    """A 100k-character run of key names, schemes, or PEM lines is redacted in linear time
    (target: under 100 ms, `redaction_timing.TARGET_SECONDS`)."""
    assert_linear(redact, build, count)
