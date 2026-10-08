"""Command line parsing for the fake MCP server: loopback by default, `--allow` forms."""

from __future__ import annotations

import pytest
from fake_mcp_server.cli import parse_allow, parse_args


def test_defaults_bind_loopback_and_hide_nothing() -> None:
    args = parse_args([])
    assert (args.host, args.port) == ("127.0.0.1", 8082)
    assert parse_allow(args.allow) is None


def test_allow_token_form_and_repeats() -> None:
    allow = parse_allow(["a=glossary_lookup,note_write", "b=glossary_lookup"])
    assert allow == {
        "a": frozenset({"glossary_lookup", "note_write"}),
        "b": frozenset({"glossary_lookup"}),
    }


def test_allow_without_token_applies_to_any_caller() -> None:
    assert parse_allow(["glossary_lookup"]) == {"*": frozenset({"glossary_lookup"})}


def test_allow_rejects_an_unknown_tool() -> None:
    with pytest.raises(SystemExit):
        parse_allow(["a=shell"])
