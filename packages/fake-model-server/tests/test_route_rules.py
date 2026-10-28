"""A rule with `model` matches only a request for that model (route). PoC-7: the evaluator gate's
fallback attempt goes to another route, so a script can answer it differently."""

from __future__ import annotations

import httpx
from fake_model_server import Script, create_app

SCRIPT = Script.model_validate(
    {
        "rules": [
            {"model": "fallback-route", "match": "simplify:", "reply": "good answer"},
            {"match": "simplify:", "reply": "bad answer"},
        ],
    }
)


def _user(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


def test_a_model_rule_matches_only_its_route() -> None:
    assert SCRIPT.pick(_user("simplify: x"), model="fallback-route").reply == "good answer"
    assert SCRIPT.pick(_user("simplify: x"), model="big-default").reply == "bad answer"
    assert SCRIPT.pick(_user("simplify: x")).reply == "bad answer"


async def test_the_server_picks_by_the_request_model() -> None:
    app = create_app(SCRIPT)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://f") as c:
        body = {"messages": _user("simplify: x")}
        good = await c.post("/v1/chat/completions", json={**body, "model": "fallback-route"})
        bad = await c.post("/v1/chat/completions", json={**body, "model": "big-default"})
    assert good.json()["choices"][0]["message"]["content"] == "good answer"
    assert bad.json()["choices"][0]["message"]["content"] == "bad answer"
