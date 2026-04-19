"""Benchmark log_event throughput (P0 baseline for P1-D).

Per-event flush is the current bottleneck; P1-D should lift throughput by
>=10x at 100k events on default buffering.
"""
from __future__ import annotations

import io

import pytest

from scripts import _log


@pytest.fixture(autouse=True)
def _reset_log():
    yield
    _log.configure(format="text")


@pytest.mark.parametrize("count", [1000, 10000, 100000])
def test_log_event_json(benchmark, count):
    buf = io.StringIO()
    _log.configure(format="json", stream=buf)

    def _run():
        for i in range(count):
            _log.log_event("spawn.container.started", task_id=f"t-{i}", index=i)

    benchmark(_run)


@pytest.mark.parametrize("count", [1000, 10000])
def test_log_event_text(benchmark, count):
    buf = io.StringIO()
    _log.configure(format="text", stream=buf)

    def _run():
        for i in range(count):
            _log.log_event("spawn.container.started", task_id=f"t-{i}")

    benchmark(_run)
