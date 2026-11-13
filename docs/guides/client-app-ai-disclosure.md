# Show that an answer is AI-generated

A guide for teams that build a client app on an agent. AI Act Art. 50(2) marking applies from 2026-12-02. The chassis marks the answer. Your app reads the mark and shows a disclosure to the user. Issue: [023 C-5](../planning/issues/023-C-5-ai-generated-marking.md). Wire detail: [contract v7, E](../contracts/contract-v7.md#e-ai-generated-marking-the-rest-of-023-c-5).

This guide is a technical guide. Whether your use case needs a disclosure is a legal decision, not a platform one.

## When an answer is marked

An agent marks its answers when `governance.risk_class` is `limited` or `high`, or when `governance.ai_generated_marking` is `true`. A `limited` or `high` agent cannot turn it off: the config is refused at load. A `minimal` agent is unmarked unless it sets the flag.

Ask the agent's owner, or read its config. Do not assume. A client that handles marked and unmarked agents must test for the mark every time.

## Where the mark is

| Place | What you get |
| ----- | ------------ |
| Response headers | `X-AI-Generated: true` and `X-AI-Disclosure-Required: true` |
| OpenAI and Anthropic bodies | `"ai_marking": {"ai_generated": true, "disclosure_required": true}` at the top level |
| Result events (for back-end consumers) | The CloudEvent extension `aigenerated: true` |

The headers are on every public run route: `/v1/run`, `/v1/chat/completions`, `/v1/messages`, and `/v1/mcp`. They are on complete answers, streams, and refusals alike. The native envelope and `/v1/mcp` have no body field, so read the headers there. `/health`, `/ready`, `/manifest`, and `/v1/feedback` are not marked.

The body field is in a complete answer and in every chunk of an OpenAI stream. In an Anthropic stream it is in the `message` of `message_start`.

## What to do

1. Read `X-AI-Disclosure-Required` on the response. When it is `true`, show a disclosure next to the answer.
2. For a stream, read the headers before the first chunk. They arrive with the response head, so you can show the disclosure before the first word.
3. If you cannot read headers, read `ai_marking.disclosure_required` in the body.
4. If a flag is missing on a route that should be marked, treat that as "disclosure required". Show it, and report the gap to the agent's owner.
5. Keep the mark when you store or forward the answer. A stored record keeps `aigenerated`.

Show the text in plain words, near the answer, in the user's language. suggested: "This answer was written by an AI system." Your legal team approves the final text.

## Example

```python
import httpx

response = httpx.post(
    "https://agent.example.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {token}"},
    json={"model": "agent", "messages": [{"role": "user", "content": "Hello"}]},
)
needs_disclosure = (
    response.headers.get("x-ai-disclosure-required") == "true"
    or response.json().get("ai_marking", {}).get("disclosure_required") is True
)
```

`token` is a caller token your platform team issues. Never put a token in the source.

## From a browser

A browser can read a custom header only when the server exposes it. The chassis adds both names to `Access-Control-Expose-Headers`, merged with any value already there. If your ingress sets that header itself, keep `X-AI-Generated` and `X-AI-Disclosure-Required` in the list.

## What a workload cannot do

The chassis sets the mark on the way out, after the workload's events come back. A workload cannot remove it. Whatever the workload sends in those headers or in `ai_marking` is overwritten.

## Evidence

- Headers on every interface, streaming and complete: `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`.
- Refusals are marked: `test_marking.py::test_a_refused_call_on_a_run_route_is_marked`.
- A workload cannot unmark: `test_marking.py::test_a_workload_that_claims_not_ai_generated_is_still_marked`.
- Body field: `packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk` and `::test_the_anthropic_body_carries_the_flags_complete_and_on_message_start`.
- Browser exposure: `test_marking_body.py::test_the_marked_response_exposes_the_marking_headers_to_browsers`.
- Result events: `test_marking_body.py::test_result_events_carry_the_aigenerated_extension`.
