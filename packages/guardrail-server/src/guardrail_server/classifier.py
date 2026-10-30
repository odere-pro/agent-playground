"""The injection classifiers behind the guardrail server.

`Classifier` is the seam: `chunks(text)` cuts a text into the windows the model reads, and
`score(chunks)` gives each window's probability of being a prompt injection. The server blocks a
text when any window scores at or over its threshold.

- `PromptGuardClassifier`: Llama Prompt Guard 2 (`meta-llama/Llama-Prompt-Guard-2-86M`) with
  Hugging Face transformers. The model reads 512 tokens, so `chunks` cuts with the model's own
  tokenizer into windows of 512 tokens with the special tokens, neighbors sharing `overlap`
  tokens. transformers and torch are imported only when it is built (the `promptguard` extra).
  The model is gated on Hugging Face: it cannot load where Hugging Face is not reachable.
- `PatternClassifier`: a regex STAND-IN for offline tests and the offline red-team run. It is not
  a model and claims nothing about Prompt Guard 2's numbers. It windows on whitespace words (512
  words, 64 shared), which is coarser than tokens: a word is one or more tokens.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

__all__ = [
    "INJECTION_PATTERNS",
    "PROMPT_GUARD_MODEL",
    "Classifier",
    "PatternClassifier",
    "PromptGuardClassifier",
]

PROMPT_GUARD_MODEL = "meta-llama/Llama-Prompt-Guard-2-86M"
WINDOW = 512
"""Prompt Guard 2's context: 512 tokens."""
OVERLAP = 64
"""suggested: tokens shared by two neighbor windows, so an injection on a boundary is whole."""


class Classifier(Protocol):
    name: str

    def chunks(self, text: str) -> list[str]: ...

    def score(self, chunks: Sequence[str]) -> list[float]: ...


# Hand copy of chassis/fakes/guardrail.py;
# packages/contract-suites/tests/test_guardrail_pattern_drift.py pins the drift.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I | re.S)
    for p in (
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|"
        r"preceding)\b.{0,20}\b(instructions?|rules?|prompts?|messages?|directions?)\b",
        r"\b(reveal|print|show|repeat|leak|output|dump|display)\b.{0,40}\b(system prompt|"
        r"instructions|api[ _-]?key|secret|password|token|environment variables?|env vars?)s?\b",
        r"\byou are now\b",
        r"\bnew (system )?instructions?\s*:",
        r"\b(send|post|upload|exfiltrate|forward|transmit)\b.{0,80}\bhttps?://",
        r"\bact as\b.{0,30}\b(unrestricted|jailbroken|developer mode|dan)\b",
        # `(?:/\s*)?`, not `/?\s*`: two adjacent `\s*` are quadratic on `<` and many spaces.
        r"<\s*(?:/\s*)?(system|im_start|im_end)\s*>",
    )
)
"""suggested: phrases of common English injections. A stand-in: it misses paraphrase, other
languages, and encodings, which is what the model is for."""


def _word_windows(text: str, window: int, overlap: int) -> list[str]:
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    if len(spans) <= window:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(spans):
        end = min(start + window, len(spans))
        out.append(text[spans[start][0] : spans[end - 1][1]])
        if end == len(spans):
            break
        start = end - overlap
    return out


class PatternClassifier:
    """The regex stand-in. Scores 1.0 for a window that matches a pattern, else 0.0."""

    name = "pattern-stand-in"

    def __init__(
        self,
        patterns: Sequence[re.Pattern[str]] = INJECTION_PATTERNS,
        *,
        window: int = WINDOW,
        overlap: int = OVERLAP,
    ) -> None:
        if window <= overlap or overlap < 0:
            raise ValueError("window must be larger than overlap, and overlap at least 0")
        self.patterns = tuple(patterns)
        self.window = window
        self.overlap = overlap

    def chunks(self, text: str) -> list[str]:
        return _word_windows(text, self.window, self.overlap)

    def score(self, chunks: Sequence[str]) -> list[float]:
        return [1.0 if any(p.search(c) for p in self.patterns) else 0.0 for c in chunks]


class PromptGuardClassifier:  # pragma: no cover - needs the gated model; see the module docstring
    """Llama Prompt Guard 2 over transformers. Loads the model in `__init__` (CPU by default)."""

    name = "prompt-guard-2"

    def __init__(
        self,
        model_id: str = PROMPT_GUARD_MODEL,
        *,
        device: str = "cpu",
        window: int = WINDOW,
        overlap: int = OVERLAP,
        batch_size: int = 8,
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "PromptGuardClassifier needs transformers and torch: "
                "install guardrail-server[promptguard]"
            ) from exc
        self._torch: Any = torch
        self._tokenizer: Any = AutoTokenizer.from_pretrained(model_id)
        self._model: Any = AutoModelForSequenceClassification.from_pretrained(model_id)
        self._model.to(device).eval()
        self._device = device
        self.window = window
        self.overlap = overlap
        self.batch_size = batch_size
        labels = {int(k): str(v).upper() for k, v in self._model.config.id2label.items()}
        malicious = [
            i for i, label in labels.items() if "MALICIOUS" in label or "INJECTION" in label
        ]
        self._malicious = malicious[0] if malicious else max(labels)
        """Prompt Guard 2 has two labels; the last one is the malicious class."""

    def chunks(self, text: str) -> list[str]:
        """Windows of `window` tokens with the special tokens, cut with the model's tokenizer."""
        ids: list[int] = self._tokenizer(text, add_special_tokens=False)["input_ids"]
        body = self.window - self._tokenizer.num_special_tokens_to_add(pair=False)
        if len(ids) <= body:
            return [text]
        out: list[str] = []
        start = 0
        while start < len(ids):
            end = min(start + body, len(ids))
            out.append(self._tokenizer.decode(ids[start:end]))
            if end == len(ids):
                break
            start = end - self.overlap
        return out

    def score(self, chunks: Sequence[str]) -> list[float]:
        torch = self._torch
        scores: list[float] = []
        for i in range(0, len(chunks), self.batch_size):
            batch = list(chunks[i : i + self.batch_size])
            inputs = self._tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.window,
            ).to(self._device)
            with torch.no_grad():
                logits = self._model(**inputs).logits
            probabilities = torch.softmax(logits, dim=-1)[:, self._malicious]
            scores.extend(float(p) for p in probabilities)
        return scores
