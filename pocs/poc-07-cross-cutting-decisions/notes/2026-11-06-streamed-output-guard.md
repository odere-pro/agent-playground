# The `output` guard stage now checks a streamed answer

Date: 2026-11-06. Review R7-04. Status: the offline checks pass. Not run on a stack or against a real LiteLLM yet.

## The gap

- The chassis ran the `output` guard stage (`GuardrailPort`, stage `output`) only when the call was not a stream. A streamed answer went only through `stream_guard.guard_stream`, the PoC-8 regex filter. That filter never calls the port.
- So a stream had no PII masking from the chassis. With `spec.guardrails.stream_secrets: false`, a secret streamed out even when `spec.guardrails.output` was `true`.
- Exit criterion 5 ("each concern works on at least two engines") did not say this. The security evidence covers complete calls only. The only record of the gap was a strict xfail, `packages/chassis/tests/test_guard_stages.py::test_output_stage_masks_pii_when_the_call_is_a_stream`, and the `secret-04-streamed-answer-has-key` row in [the red-team note](2026-10-28-redteam.md).

## The fix

The design came from `chassis-architect`: a sentence window through the guardrail port.

- When the `output` stage is on (a guardrail port other than `NoGuardrails`, and `spec.guardrails.output: true`), `guard_stream` collects the text that the regex filter releases. It sends that text to the port at stage `output`, one window at a time, through `guard.check_text`. That wrapper calls the same `_check` as the complete-call stage, so `on_unavailable`, the counters, and the logs work the same way.
- A window closes:
  - at a sentence end (`.`, `!`, or `?` followed by whitespace) or a newline, at the end of the first delta that sits on whitespace;
  - before any non-delta event;
  - at `end`, or when the engine stream ends;
  - at `PORT_WINDOW_CHARS`, at the last whitespace inside that cap.
- A window breaks only at whitespace, so a masked text lines up with the text it replaces.
- A clean window: the engine's deltas leave unchanged.
- A masked window: one `delta` with the masked text.
- `end.output.text` also goes through the port, and is masked or blocks.
- A blocked window: the window is dropped and the engine stream is closed. The last event is `error guardrail_blocked` (not retryable, the fixed public text), with no `end`. Earlier windows have already left. It is counted as `chassis.guardrail_blocked{stage="output", reason=...}` and logged with ids and reasons only.
- The regex check still runs first. A key is still stopped at stage `output_stream`. When the regex filter blocks, the open window is dropped without a port call.
- An outage with `fail_closed`: the last event is `error guardrail_unavailable` (retryable).
- An outage with `fail_open`: the window passes and `chassis.guardrail_unavailable{stage="output"}` is counted. The rest of that stream, the end text included, skips the port. A pass like this carries the verdict reason `guard.UNCHECKED`.
- With `NoGuardrails`, or with `output: false`, nothing changes and the port is never called.
- Code: `packages/chassis/src/chassis/server/stream_guard.py` (`PortWindow`, `guard_stream` now takes the `PortBundle`), `guard.py` (`check_text`, `UNCHECKED`), and `pipeline.py` (the stream is wrapped when `stream_guard_on(guards) or guard.enabled(ports, guards, "output")`).

## Limits

- Text that has left is never pulled back. A window that left before a block stays with the client.
- PII that spans two windows can pass. That happens when the PII has a newline or a sentence end inside it, or when it crosses the `PORT_WINDOW_CHARS` cut. Nothing behind this window catches it: LiteLLM's `pii-mask` runs `pre_call`, so it masks the model's input, not its answer (platform-security review, 2026-11-06). Running `pii-mask` on output too is a follow-up, not done here.
- A word with no whitespace that is longer than the cap is sent as one window, so that window can be longer than `PORT_WINDOW_CHARS`. A single delta that holds many sentences is one window too, because deltas are not split at a sentence end.
- Time to first delta: with the `output` stage on, the first delta leaves after the first sentence (or newline, or 512 characters) plus one port round trip, not after the first word. Every later sentence waits for its own port call. Not measured. With the stage off, the latency is the same as before.
- The port is called once per sentence. A long answer means many guardrail calls per stream.
- `KeywordGuardrails` and the guardrail server are regex stand-ins. A real Presidio or Prompt Guard 2 has not checked a stream yet.

## Suggested values

- `PORT_WINDOW_CHARS = 512`: suggested. It is the longest text one port call sees when no sentence ends.
- After a `fail_open` outage, the rest of the stream skips the port: suggested. One outage then does not add a timeout to every sentence.

## Effect on the PoC-7 red-team record

`secret-04-streamed-answer-has-key` is now stopped under the PoC-7 controls (`CONTROLS["poc07"]`: `stream_secrets: false`) on both stacks, because the `output` stage reads the stream. The rerun gives 13/18 on `litellm` and 12/18 on `keyword`. The record says 12/18 and 11/18. So `tests/test_poc07_redteam.py::test_the_run_matches_the_recorded_pass_rates` fails until [the results file](2026-10-28-redteam-results.json) is re-recorded. This note does not re-record it or change criterion 7's text. That is the owner's decision.

## How to rerun

```bash
scripts/check_offline.sh packages/chassis/tests/test_guard_stages.py packages/chassis/tests/test_output_filter.py
make test-poc POC=07
make test-poc POC=08
```

The new tests are in `packages/chassis/tests/test_guard_stages.py`:

- `test_output_stage_masks_pii_when_the_call_is_a_stream` (no longer xfail)
- `test_a_streamed_masked_window_is_one_delta_and_the_end_text_is_masked`
- `test_a_streamed_secret_is_blocked_by_the_output_stage_without_stream_secrets`
- `test_the_regex_check_still_stops_a_streamed_key_first`
- `test_a_stream_port_outage_fail_closed_ends_in_retryable_unavailable`
- `test_a_stream_port_outage_fail_open_passes_and_skips_the_port_after`
- `test_three_streamed_sentences_are_three_output_calls_plus_the_end_text`
- `test_a_long_streamed_answer_without_a_sentence_end_is_checked_in_windows`
- `test_a_stream_without_the_output_stage_never_calls_the_port[no_port]`, `[output_off]`
