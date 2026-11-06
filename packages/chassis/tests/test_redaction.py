"""PoC-7: `chassis.redaction` stays linear on long input and leaves non-PII numbers alone
(review R1-01 and R1-04)."""

from __future__ import annotations

import time

import pytest
from chassis.redaction import redact

FAST_SECONDS = 0.1
"""suggested: the bound for one `redact` call on a 200k-character string."""


@pytest.mark.parametrize(
    "text",
    [
        "a-" * 100_000,
        "1" * 200_000,
        "x@" + "a" * 200_000,
        "x@" + "a." * 100_000,
        "a@" * 100_000,
    ],
    ids=["a-dash-run", "digit-run", "long-domain", "long-dotted-domain", "many-at-signs"],
)
def test_redact_returns_fast_and_unchanged_when_long_text_holds_no_email(text: str) -> None:
    """A long run with no full email address comes back unchanged in under 100 ms."""
    start = time.perf_counter()

    out = redact(text)

    assert time.perf_counter() - start < FAST_SECONDS
    assert out == text


def test_redact_masks_an_email_fast_when_its_domain_is_long_and_dotted() -> None:
    """An email with a 200k-character dotted domain is masked in under 100 ms."""
    text = "x@" + "a." * 100_000 + "com"
    start = time.perf_counter()

    out = redact(text)

    assert time.perf_counter() - start < FAST_SECONDS
    assert out == "<EMAIL_ADDRESS>"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mail jane.doe@example.com.", "Mail <EMAIL_ADDRESS>."),
        ("a+b@x.io,c_d@y.org", "<EMAIL_ADDRESS>,<EMAIL_ADDRESS>"),
        ("<ops-1@mail.example.co.uk>", "<<EMAIL_ADDRESS>>"),
    ],
    ids=["sentence", "two-in-a-row", "in-brackets"],
)
def test_redact_masks_the_same_emails_when_the_pattern_is_anchored(
    text: str, expected: str
) -> None:
    """The left anchor does not change which emails are masked."""
    out = redact(text)

    assert out == expected


def test_redact_masks_two_emails_when_the_second_starts_where_the_first_ends() -> None:
    """An email that starts right after another one is still masked, as before the anchor."""
    text = "a@b.com1x@y.org"

    out = redact(text)

    assert out == "<EMAIL_ADDRESS><EMAIL_ADDRESS>"


@pytest.mark.parametrize(
    "text",
    [
        "released 2026-10-29",
        "ip 192.168.100.200",
        "host 10.0.0.12",
        "version 1.24.3",
        "version 2.0.10",
        "version 12.34.56",
        "order 1234567890",
        "order 14155550100",
        "ref 4111111111111112",
        "ref 41111111111111123",
    ],
    ids=[
        "iso-date",
        "ipv4",
        "ipv4-short-groups",
        "version",
        "version-two-digit-patch",
        "version-two-digit-groups",
        "ten-digit-order-id",
        "eleven-digit-order-id",
        "sixteen-digits-failing-luhn",
        "seventeen-digits-failing-luhn",
    ],
)
def test_redact_leaves_dates_ips_versions_and_order_ids_alone(text: str) -> None:
    """An ISO date, an IPv4 address, a dotted version, or a bare digit run that fails the Luhn
    check is not a phone or a card number."""
    out = redact(text)

    assert out == text


def test_redact_leaves_the_spec_sentence_unchanged_when_it_holds_no_pii() -> None:
    """A sentence with only a date, an IPv4 address, and an order id comes back as it went in."""
    text = "released 2026-10-29 from 10.0.0.12, order 1234567890"

    out = redact(text)

    assert out == text


@pytest.mark.parametrize(
    "phone",
    [
        "+1 415 555 0100",
        "+44 20 7946 0958",
        "+31 20 123 4567",
        "(415) 555-0100",
        "415-555-0100",
        "415.555.0100",
        "+14155550100",
        "030-1234-5678",
        "06.12.34.56.78",
    ],
    ids=[
        "us-intl",
        "uk-intl",
        "nl-intl",
        "us-parens",
        "us-dashes",
        "us-dots",
        "e164",
        "de-dashes",
        "fr-dots",
    ],
)
def test_redact_masks_a_phone_number_when_written_in_a_common_format(phone: str) -> None:
    """A real phone number in any common format is replaced by `<PHONE_NUMBER>`."""
    text = f"call {phone} today"

    out = redact(text)

    assert phone.lstrip("(") not in out
    assert "<PHONE_NUMBER>" in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ref 4111111111111111", "ref <CREDIT_CARD>"),
        ("card 4111-1111-1111-1111 ok", "card <CREDIT_CARD> ok"),
        ("amex 378282246310005", "amex <CREDIT_CARD>"),
    ],
    ids=["bare-sixteen-digits", "dashed-sixteen-digits", "fifteen-digit-amex"],
)
def test_redact_masks_a_card_number_when_it_passes_the_luhn_check(text: str, expected: str) -> None:
    """A 13 to 19 digit run that passes the Luhn check is `<CREDIT_CARD>`, with or without
    separators."""
    out = redact(text)

    assert out == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ref 4111 1111 1111 1112 ok", "ref <PHONE_NUMBER> ok"),
        ("ref 4111-1111-1111-1112 ok", "ref <PHONE_NUMBER> ok"),
        ("ref +4111111111111112 ok", "ref <PHONE_NUMBER> ok"),
    ],
    ids=["spaced", "dashed", "plus-sign"],
)
def test_redact_masks_a_luhn_failing_run_as_a_phone_when_it_has_separators_or_a_plus(
    text: str, expected: str
) -> None:
    """A run that fails the Luhn check is not a card. It is still a phone when it has a leading
    `+` or a separator, by the phone rule."""
    out = redact(text)

    assert out == expected


def test_redact_masks_a_card_number_when_it_is_sixteen_digits_in_groups_of_four() -> None:
    """A Luhn-valid 16-digit card written with spaces is still `<CREDIT_CARD>`."""
    text = "card 4111 1111 1111 1111."

    out = redact(text)

    assert out == "card <CREDIT_CARD>."


@pytest.mark.parametrize(
    "phone",
    [
        "123.456.789",
        "612.345.678",
        "1.800.555.019",
        "06.12.34.56",
        "020.794.095",
        "1234-56-78",
        "0171-12-34",
        "8800-55-35",
    ],
    ids=[
        "three-dotted-triples",
        "nine-digit-dotted",
        "toll-free-dotted",
        "fr-short-dotted",
        "leading-zero-dotted",
        "invalid-date-month",
        "dashed-landline",
        "dashed-toll-free",
    ],
)
def test_redact_masks_a_phone_number_when_it_looks_dotted_or_dated_but_is_not(phone: str) -> None:
    """A dotted or dashed run that is not a valid date, IPv4 address, or x.y.z version is masked."""
    text = f"call {phone} now"

    out = redact(text)

    assert out == "call <PHONE_NUMBER> now"
