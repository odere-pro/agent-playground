"""`results.json` and `results.md`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from bakeoff.runner import Cell

__all__ = ["render_json", "render_markdown"]


def _num(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_json(cells: Sequence[Cell], meta: dict[str, Any]) -> str:
    rows = [
        {
            "engine": cell.engine,
            "lane": cell.lane,
            "status": cell.status,
            "reason": cell.reason,
            "mapping_code_lines": cell.code_lines,
            "mapping_files": cell.mapping_files,
            "tasks": {name: result.to_json() for name, result in cell.tasks.items()},
        }
        for cell in cells
    ]
    return json.dumps({"meta": meta, "cells": rows}, indent=2, sort_keys=True) + "\n"


def _calls(calls: Sequence[dict[str, Any]]) -> tuple[str, str]:
    if not calls:
        return "-", "-"
    sizes = "/".join(str(c["prompt_bytes"]) for c in calls)
    keys = "; ".join(",".join(c["body_keys"]) for c in calls[:1])
    return sizes, keys


def render_markdown(cells: Sequence[Cell], meta: dict[str, Any]) -> str:
    out = ["# Bake-off results", ""]
    out.append(
        f"Tasks: {', '.join(meta['tasks'])}. Repeat: {meta['repeat']}. "
        f"Model: {meta['model']}. Generated: {meta['generated']}."
    )
    out += [
        "",
        "| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms "
        "| In tok | Out tok | Prompt bytes per call | Request body keys |",
        "| ------ | ---- | ---- | ---- | ---------- | ------ | ------ | ----------- "
        "| ------ | ------- | --------------------- | ----------------- |",
    ]
    skipped: list[Cell] = []
    for cell in cells:
        if cell.status != "ok":
            skipped.append(cell)
            continue
        for name, result in cell.tasks.items():
            data = result.to_json()
            tools = data["tool_call_pass_rate"]
            sizes, keys = _calls(data["model_calls"])
            out.append(
                f"| {cell.engine} | {cell.lane} | {name} "
                f"| {result.passed}/{result.runs} "
                f"| {'-' if tools is None else f'{result.tools_passed}/{result.runs}'} "
                f"| {_num(data['latency_p50_ms'])} | {_num(data['latency_p95_ms'])} "
                f"| {_num(data['ttft_p50_ms'])} "
                f"| {_num(data['input_tokens'], 0)} | {_num(data['output_tokens'], 0)} "
                f"| {sizes} | {keys} |"
            )
    if skipped:
        out += [
            "",
            "## Not run",
            "",
            "| Engine | Lane | Status | Reason |",
            "| --- | --- | --- | --- |",
        ]
        out += [f"| {c.engine} | {c.lane} | {c.status.upper()} | {c.reason} |" for c in skipped]
    seen: dict[str, Cell] = {}
    for cell in cells:
        seen.setdefault(cell.engine, cell)
    out += [
        "",
        "## Mapping size",
        "",
        "| Engine | Code lines | Files |",
        "| ------ | ---------- | ----- |",
    ]
    for engine, cell in seen.items():
        files = ", ".join(f.rsplit("/", 1)[-1] for f in cell.mapping_files) or "-"
        lines = "-" if cell.code_lines is None else str(cell.code_lines)
        out.append(f"| {engine} | {lines} | {files} |")
    failures = [(c, n, r.failures) for c in cells for n, r in c.tasks.items() if r.failures]
    if failures:
        out += ["", "## Failures", ""]
        out += [f"- {c.engine} {c.lane} {n}: {'; '.join(sorted(set(f)))}" for c, n, f in failures]
    return "\n".join(out) + "\n"
