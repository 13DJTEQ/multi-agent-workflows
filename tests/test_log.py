"""Tests for scripts/_log.py."""

from __future__ import annotations

import io
import json

import pytest

from scripts import _log


@pytest.fixture(autouse=True)
def reset_config():
    """Ensure each test starts with clean state (default text format, stderr)."""
    yield
    _log.configure(format="text")


class TestConfigure:
    def test_invalid_format_rejected(self):
        with pytest.raises(ValueError, match="log format"):
            _log.configure(format="yaml")

    def test_valid_formats(self):
        _log.configure(format="text")
        _log.configure(format="json")

    def test_custom_stream(self):
        buf = io.StringIO()
        _log.configure(format="json", stream=buf)
        _log.log_event("test.event", x=1)
        record = json.loads(buf.getvalue().splitlines()[0])
        assert record["event"] == "test.event"
        assert record["x"] == 1


class TestJsonFormat:
    def test_single_event_one_line(self):
        buf = io.StringIO()
        _log.configure(format="json", stream=buf)
        _log.log_event("spawn.start", backend="docker", tasks=3)
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "spawn.start"
        assert rec["backend"] == "docker"
        assert rec["tasks"] == 3
        assert "ts" in rec
        assert isinstance(rec["ts"], float)

    def test_multiple_events_produce_ndjson(self):
        buf = io.StringIO()
        _log.configure(format="json", stream=buf)
        _log.log_event("a")
        _log.log_event("b", k="v")
        _log.log_event("c")
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 3
        events = [json.loads(ln)["event"] for ln in lines]
        assert events == ["a", "b", "c"]

    def test_timestamps_monotonic(self):
        buf = io.StringIO()
        _log.configure(format="json", stream=buf)
        for _ in range(5):
            _log.log_event("tick")
        times = [json.loads(ln)["ts"] for ln in buf.getvalue().splitlines() if ln]
        assert times == sorted(times), "timestamps should be monotonic non-decreasing"

    def test_nonserializable_falls_back(self):
        buf = io.StringIO()
        _log.configure(format="json", stream=buf)

        class NotSerializable:
            def __repr__(self):
                return "<thing>"

        _log.log_event("weird", thing=NotSerializable())
        # Should not raise; line should still be valid JSON
        line = buf.getvalue().splitlines()[0]
        rec = json.loads(line)
        assert rec["event"] == "weird"
        # Either the default=str serializer handled it or fallback path kicked in
        assert "thing" in rec or "error" in rec


class TestTextFormat:
    def test_text_format_has_event_name(self):
        buf = io.StringIO()
        _log.configure(format="text", stream=buf)
        _log.log_event("spawn.start", tasks=3)
        out = buf.getvalue()
        assert "spawn.start" in out
        assert "tasks=3" in out

    def test_text_format_iso_timestamp(self):
        buf = io.StringIO()
        _log.configure(format="text", stream=buf)
        _log.log_event("x")
        line = buf.getvalue().splitlines()[0]
        assert line.startswith("[")
        # ISO-ish: [YYYY-MM-DDTHH:MM:SSZ]
        assert "T" in line.split("]")[0]


class TestArgparseHelper:
    def test_add_log_format_arg_defaults_to_text(self):
        import argparse

        p = argparse.ArgumentParser()
        _log.add_log_format_arg(p)
        args = p.parse_args([])
        assert args.log_format == "text"
        assert args.log_flush_each is False

    def test_add_log_format_arg_accepts_json(self):
        import argparse

        p = argparse.ArgumentParser()
        _log.add_log_format_arg(p)
        args = p.parse_args(["--log-format", "json"])
        assert args.log_format == "json"

    def test_add_log_format_arg_rejects_unknown(self):
        import argparse

        p = argparse.ArgumentParser()
        _log.add_log_format_arg(p)
        with pytest.raises(SystemExit):
            p.parse_args(["--log-format", "yaml"])

    def test_add_log_format_arg_flush_each_flag(self):
        import argparse

        p = argparse.ArgumentParser()
        _log.add_log_format_arg(p)
        args = p.parse_args(["--log-flush-each"])
        assert args.log_flush_each is True


class TestBufferedFlush:
    """P1-D: flush policy. Buffered by default, atexit-safe, replay-complete."""

    def test_buffered_does_not_flush_every_event(self):
        class CountingBuffer(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flush_count = 0

            def flush(self):
                self.flush_count += 1
                super().flush()

        cb = CountingBuffer()
        _log.configure(format="json", stream=cb, flush_interval_events=10000, flush_interval_seconds=9999)
        for i in range(20):
            _log.log_event("spawn.start", i=i)
        # No flush should have triggered (below thresholds)
        assert cb.flush_count == 0

    def test_events_threshold_triggers_flush(self):
        class CountingBuffer(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flush_count = 0

            def flush(self):
                self.flush_count += 1
                super().flush()

        cb = CountingBuffer()
        _log.configure(format="json", stream=cb, flush_interval_events=5, flush_interval_seconds=9999)
        for i in range(12):
            _log.log_event("tick", i=i)
        # 12 events / 5-event threshold = 2 threshold-triggered flushes
        assert cb.flush_count >= 2

    def test_flush_each_legacy_behavior(self):
        class CountingBuffer(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flush_count = 0

            def flush(self):
                self.flush_count += 1
                super().flush()

        cb = CountingBuffer()
        _log.configure(format="json", stream=cb, flush_each=True)
        for _ in range(7):
            _log.log_event("tick")
        assert cb.flush_count == 7

    def test_manual_flush_drains(self):
        buf = io.StringIO()
        _log.configure(format="json", stream=buf, flush_interval_events=10000, flush_interval_seconds=9999)
        for i in range(3):
            _log.log_event("tick", i=i)
        _log.flush()
        # All 3 events must be present in buffer after manual flush
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 3

    def test_zero_event_loss_replay(self):
        """All buffered events must be in the stream after flush() — no drop."""
        buf = io.StringIO()
        _log.configure(format="json", stream=buf, flush_interval_events=100, flush_interval_seconds=9999)
        for i in range(57):
            _log.log_event("replay", i=i)
        _log.flush()
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 57
        indices = [json.loads(ln)["i"] for ln in lines]
        assert indices == list(range(57))

    def test_atexit_registered_once(self):
        """Re-configuring does not register the atexit flush twice."""
        _log.configure(format="json")
        _log.configure(format="json")
        # Not directly observable without poking atexit internals; use the
        # module-level sentinel instead.
        assert _log._atexit_registered is True
