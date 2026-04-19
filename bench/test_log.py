"""Benchmark log_event throughput.

StringIO flush is a no-op, so the StringIO variants don't expose the real
flush cost. The file-backed variants call fsync-less flush() on a real file
descriptor, which is representative of stderr in production.

P1-D target: buffered mode >=10x faster than flush-each on the file-backed
100k-events scenario; zero event loss.
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


@pytest.mark.parametrize("count", [10000, 100000])
def test_log_event_json_file_flush_each(benchmark, count, tmp_path):
    """Baseline: legacy flush-each behavior on a real file descriptor."""
    path = tmp_path / "events.ndjson"

    def _run():
        with open(path, "w") as fh:
            _log.configure(format="json", stream=fh, flush_each=True)
            for i in range(count):
                _log.log_event("evt", i=i)

    benchmark(_run)


@pytest.mark.parametrize("count", [10000, 100000])
def test_log_event_json_file_buffered(benchmark, count, tmp_path):
    """P1-D: buffered flush on same file. Target >=10x over flush-each."""
    path = tmp_path / "events.ndjson"

    def _run():
        with open(path, "w") as fh:
            _log.configure(
                format="json", stream=fh,
                flush_interval_events=1000, flush_interval_seconds=60.0,
            )
            for i in range(count):
                _log.log_event("evt", i=i)
            _log.flush()

    benchmark(_run)
