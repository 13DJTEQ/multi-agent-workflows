"""Shared fixtures and helpers for bench/ pytest-benchmark runs.

The generators here produce deterministic synthetic envelopes so benchmark
results are reproducible across machines and CI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest


def _envelope(i: int, status: str = "ok", with_metrics: bool = True) -> dict:
    env = {
        "schema_version": "1",
        "status": status,
        "task_id": f"task-{i:06d}",
        "data": {"index": i, "payload": f"synthetic-payload-{i}" * 4},
    }
    if with_metrics:
        env["metrics"] = {
            "duration_seconds": 0.5 + (i % 100) * 0.01,
            "tokens_used": 100 + (i % 500),
            "cost_usd": 0.001 * (1 + (i % 50)),
            "model": "claude-opus-4" if i % 3 else "gpt-4",
        }
    return env


def write_envelope_dir(dirpath: Path, count: int, failure_rate: float = 0.05) -> Path:
    """Write `count` envelope files under `dirpath/<task_id>/result.json`.

    Deterministic: same count + failure_rate always produces the same corpus.
    """
    dirpath.mkdir(parents=True, exist_ok=True)
    every_nth_failure = int(1 / failure_rate) if failure_rate > 0 else 0
    for i in range(count):
        status = "failed" if every_nth_failure and i % every_nth_failure == 0 else "ok"
        sub = dirpath / f"task-{i:06d}"
        sub.mkdir(exist_ok=True)
        (sub / "result.json").write_text(json.dumps(_envelope(i, status=status)))
    return dirpath


def envelopes(count: int, failure_rate: float = 0.05) -> list[dict]:
    """Produce `count` in-memory envelopes; deterministic."""
    every_nth_failure = int(1 / failure_rate) if failure_rate > 0 else 0
    return [
        _envelope(
            i,
            status="failed" if every_nth_failure and i % every_nth_failure == 0 else "ok",
        )
        for i in range(count)
    ]


@pytest.fixture
def tmp_envelope_dir(tmp_path) -> Path:
    """Empty directory for a benchmark run to write into."""
    d = tmp_path / "envelopes"
    d.mkdir()
    return d


@pytest.fixture
def envelope_factory():
    """Expose write_envelope_dir + in-memory generator as a fixture."""
    return type(
        "_F",
        (),
        {"write_dir": staticmethod(write_envelope_dir), "make": staticmethod(envelopes)},
    )
