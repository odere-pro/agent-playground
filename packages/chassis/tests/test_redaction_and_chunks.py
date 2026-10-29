"""PoC-7: the chassis's regex redaction and the classifier chunking rule."""

from __future__ import annotations

from chassis.ports.guardrail import split_for_classifier
from chassis.redaction import redact, redact_value


def test_redact_masks_emails_phones_and_ids() -> None:
    text = (
        "Mail jane.doe@example.com, call +1 415 555 0134 or 030-1234-5678, "
        "card 4111 1111 1111 1111, IBAN DE89 3704 0044 0532 0130 00, SSN 123-45-6789."
    )
    out = redact(text)
    for value in (
        "jane.doe@example.com",
        "415 555 0134",
        "1234-5678",
        "4111 1111 1111 1111",
        "DE89 3704",
        "123-45-6789",
    ):
        assert value not in out
    for entity in ("EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "US_SSN"):
        assert f"<{entity}>" in out


def test_redact_keeps_plain_numbers_and_ids_the_chassis_needs() -> None:
    text = "The SLM cut costs by 30 percent in 2026. request_id 3f2a9c1e7b6d4e5f"
    assert redact(text) == text


def test_redact_value_walks_nested_data() -> None:
    data = {"a": ["x jane@example.com"], "b": {"c": ("+44 20 7946 0958",)}, "n": 3}
    out = redact_value(data)
    assert out == {"a": ["x <EMAIL_ADDRESS>"], "b": {"c": ("<PHONE_NUMBER>",)}, "n": 3}


def test_short_text_is_one_chunk() -> None:
    assert split_for_classifier("one two three") == ["one two three"]


def test_long_text_chunks_overlap_and_cover_everything() -> None:
    words = [f"w{i}" for i in range(1000)]
    chunks = split_for_classifier(" ".join(words), pieces=100, overlap=10)
    assert all(len(c.split()) <= 100 for c in chunks)
    assert chunks[0].split()[-10:] == chunks[1].split()[:10]
    seen = {w for c in chunks for w in c.split()}
    assert seen == set(words)
