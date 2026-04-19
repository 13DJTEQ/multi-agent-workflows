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
