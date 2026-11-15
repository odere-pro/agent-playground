"""PoC-5 on kind: H31, a spoofed agent tag (004 G-2; blind-spots note, section 3).

004 G-2: "A call that sends another agent's name in its metadata is still counted under the agent
of its key." The agent is the `agent:` tag in the calling virtual key's `metadata.tags`
(`deploy/kind/poc05/platform/seed.sh`, `key_body`), never the request's metadata. The test calls
the real LiteLLM with svc-echo's key (agent `echo`) and metadata that names `echo-remote`, an
agent with its own live key on this cluster. Then it runs G-2's one usage query,
`deploy/compose/poc08-litellm/usage_totals.sql`, against this cluster's Postgres, over a window
that holds only that call.

Marked `network` and skipped unless `KIND_DRILLS=1` (`poc_harness.drills`). It needs the
`with-gateway` wrapper, which exports `POC05_GATEWAY_MCP_URL` (a port-forward to LiteLLM) and
`POC05_CHASSIS_VIRTUAL_KEY` (svc-echo's key, from its Secret, never in argv or output):

    KIND_DRILLS=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network \\
        pocs/poc-05-sandboxed/tests/test_poc05_kind_spoofed_tag.py -q -rs

The key never leaves this process: an error shows the status and LiteLLM's message with every
`sk-` run removed. Postgres is read through `kubectl exec` with `psql` on its own socket.
"""

from __future__ import annotations

import csv
import io
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from poc_harness.drills import KIND_SANDBOX, required_env
from poc_harness.kind import PLATFORM_NS, kubectl

pytestmark = KIND_SANDBOX

ROOT = Path(__file__).resolve().parents[3]
USAGE_SQL = ROOT / "deploy/compose/poc08-litellm/usage_totals.sql"
URL_VAR = "POC05_GATEWAY_MCP_URL"
KEY_VAR = "POC05_CHASSIS_VIRTUAL_KEY"
KEY_ALIAS = "svc-echo"
KEY_AGENT = "echo"
"""svc-echo's tag is `agent:echo` (seed.sh `SERVICES`, `key_body`)."""
SPOOFED = "echo-remote"
"""Another agent with its own live key here (svc-echo-remote), so the spoof names a real agent."""
ROUTE = "fake-chat"
WAIT_S = 90.0
"""suggested: how long to wait for LiteLLM's batched spend-log write."""
POLL_S = 2.0
TIMEOUT_S = 60.0

SPEND_ROW = """
SELECT s.request_id, s.end_user, s.request_tags::text AS request_tags,
       s."startTime"::text AS start_time, v.key_alias, (v.metadata -> 'tags')::text AS key_tags
FROM "LiteLLM_SpendLogs" s
LEFT JOIN "LiteLLM_VerificationToken" v ON v.token = s.api_key
WHERE s.end_user = :'user';
"""


def _litellm(mcp_url: str) -> str:
    """LiteLLM's base URL from the gateway's MCP URL (`http://127.0.0.1:<port>/mcp/`)."""
    return mcp_url.rstrip("/").removesuffix("/mcp")


def _scrub(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-<redacted>", text)[:300]


def _psql(sql: str, **variables: str) -> list[dict[str, str]]:
    """Rows of `sql`, run by psql in the Postgres pod as `litellm` on its own socket."""
    settings = [part for k, v in variables.items() for part in ("-v", f"{k}={v}")]
    got = kubectl(
        "exec", "-i", "-n", PLATFORM_NS, "deploy/postgres", "-c", "postgres", "--",
        "psql", "-X", "-q", "--csv", "-v", "ON_ERROR_STOP=1", *settings,
        "-U", "litellm", "-d", "litellm", "-f", "-",
        stdin=sql,
    )  # fmt: skip
    assert got.returncode == 0, got.stderr[-400:]
    return list(csv.DictReader(io.StringIO(got.stdout)))


def _spend_rows(user: str) -> list[dict[str, str]]:
    """The spend-log rows of `user`, waited for: LiteLLM writes them in batches."""
    deadline = time.monotonic() + WAIT_S
    rows = _psql(SPEND_ROW, user=user)
    while not rows and time.monotonic() < deadline:
        time.sleep(POLL_S)
        rows = _psql(SPEND_ROW, user=user)
    return rows


def _spoofed_call(user: str) -> dict[str, Any]:
    """One chat call on svc-echo's key whose metadata names another agent, tag and field."""
    mcp_url, key = required_env(URL_VAR, KEY_VAR)
    body = {
        "model": ROUTE,
        "messages": [{"role": "user", "content": "hello"}],
        "user": user,
        "metadata": {"tags": [f"agent:{SPOOFED}"], "agent": SPOOFED},
    }
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=_litellm(mcp_url), timeout=TIMEOUT_S, trust_env=False) as client:
        answer = client.post("/v1/chat/completions", json=body, headers=headers)
    del key, headers
    assert answer.status_code == 200, (answer.status_code, _scrub(answer.text))
    return dict(answer.json())


@pytest.fixture(scope="module")
def spoofed() -> dict[str, Any]:
    """The call, its one spend-log row, and G-2's usage query over a window of that row only."""
    user = f"h31-{uuid.uuid4().hex[:8]}"
    answer = _spoofed_call(user)
    rows = _spend_rows(user)
    assert len(rows) == 1, rows
    start = rows[0]["start_time"]
    window = {"from": f"'{start}'", "to": f"'{start}'::timestamp + interval '1 millisecond'"}
    totals = _psql(USAGE_SQL.read_text(), **window)
    return {"user": user, "answer": answer, "row": rows[0], "totals": totals}


def test_spoofed_agent_tag_counts_under_key_agent(spoofed: dict[str, Any]) -> None:
    """A call that sends another agent's name in its metadata is still counted under the agent
    of its key.

    H31 on kind. The paired control: the spoof reached the router (the row's request tags hold
    `agent:echo-remote`), and the row is on svc-echo's key, tagged `agent:echo`. G-2's usage
    query over a window that holds only that row counts it under `echo`, once, and lists no
    `echo-remote` agent."""
    row, user = spoofed["row"], spoofed["user"]

    by = {(t["level"], t["name"]): t for t in spoofed["totals"]}

    assert row["request_id"] == spoofed["answer"]["id"], row
    assert f"agent:{SPOOFED}" in row["request_tags"], row
    assert row["key_alias"] == KEY_ALIAS, row
    assert f'"agent:{KEY_AGENT}"' in row["key_tags"], row
    assert by[("user", user)]["calls"] == "1", by
    assert by[("agent", KEY_AGENT)]["calls"] == "1", by
    assert ("agent", SPOOFED) not in by, by
