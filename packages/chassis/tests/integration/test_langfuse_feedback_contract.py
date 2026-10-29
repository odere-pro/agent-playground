"""`LangfuseFeedback` against a real Langfuse (PoC-7, exit criterion 2): the same
`FeedbackPortContract` as the fake and the offline binding. `network`; it runs only when
`LANGFUSE_BASE_URL` (or `LANGFUSE_HOST`), `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` are
set, against a project made for tests (the Mac compose stack, or CI's service container).

Langfuse ingests scores asynchronously (202), so a read polls `GET /api/public/v3/scores?id=...`
for up to `READ_WAIT_S`. No outage case: a real service cannot be made to fail on cue.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from chassis.adapters.langfuse import LangfuseFeedback, feedback_from_score
from chassis.ports.feedback import Feedback
from chassis_contracts import FeedbackPortContract

READ_WAIT_S = 30.0
"""suggested: how long a read waits for Langfuse to ingest a score."""

pytestmark = pytest.mark.skipif(
    not (
        (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST"))
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    ),
    reason="needs LANGFUSE_BASE_URL or LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY",
)


class _Recording:
    """The real adapter, remembering what each `feedback_id` was sent with: Langfuse sets a
    `timestamp` when none was sent, and the suite compares with what was sent."""

    name = "langfuse"

    def __init__(self, port: LangfuseFeedback) -> None:
        self.port = port
        self.sent: dict[str, Feedback] = {}

    async def submit(self, feedback: Feedback) -> None:
        await self.port.submit(feedback)
        self.sent[feedback.feedback_id] = feedback


def _scores(feedback_id: str) -> list[dict[str, Any]]:
    port = LangfuseFeedback.from_env()
    env = os.environ
    with httpx.Client(
        base_url=port.host,
        auth=(env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"]),
        timeout=10,
    ) as client:
        deadline = time.monotonic() + READ_WAIT_S
        while True:
            answer = client.get(
                "/api/public/v3/scores", params={"id": feedback_id, "fields": "details,subject"}
            )
            answer.raise_for_status()
            data: list[dict[str, Any]] = answer.json()["data"]
            if data or time.monotonic() > deadline:
                return data
            time.sleep(1)


class TestLangfuseFeedbackLive(FeedbackPortContract):
    @pytest.fixture
    def feedback_port(self) -> _Recording:
        return _Recording(LangfuseFeedback.from_env())

    @pytest.fixture
    def read_feedback(self, feedback_port: _Recording) -> Callable[[str], Feedback | None]:
        def read(feedback_id: str) -> Feedback | None:
            found = _scores(feedback_id)
            if not found:
                return None
            feedback = feedback_from_score(found[0])
            sent = feedback_port.sent.get(feedback_id)
            if sent is not None and sent.timestamp is None:
                feedback = feedback.model_copy(update={"timestamp": None})
            return feedback

        return read

    @pytest.fixture
    def count_feedback(self) -> Callable[[str], int]:
        return lambda feedback_id: len(_scores(feedback_id))
