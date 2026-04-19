"""Tests for scripts/spawn_oz.py."""
from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from scripts import spawn_oz


class TestParseRunId:
    def test_parses_run_id_from_json(self):
        out = '{"run_id": "5972cca4-a410-42af-930a-e56bc23e07ac", "status": "pending"}'
        assert spawn_oz._parse_run_id(out) == "5972cca4-a410-42af-930a-e56bc23e07ac"

    def test_parses_id_fallback_field(self):
        out = '{"id": "abc12345-a410-42af-930a-e56bc23e07ac"}'
        assert spawn_oz._parse_run_id(out) == "abc12345-a410-42af-930a-e56bc23e07ac"

    def test_parses_uuid_from_text_output(self):
        out = "Spawned agent with run ID: 5972CCA4-A410-42AF-930A-E56BC23E07AC\nDone."
        assert spawn_oz._parse_run_id(out) == "5972CCA4-A410-42AF-930A-E56BC23E07AC"

    def test_returns_none_on_empty(self):
        assert spawn_oz._parse_run_id("") is None

    def test_returns_none_on_unparseable(self):
        assert spawn_oz._parse_run_id("some random output with no uuid") is None


class TestGenerateTaskId:
    def test_includes_phase(self):
        tid = spawn_oz.generate_task_id("Analyze the auth module", 3, phase="phase1")
        assert tid.startswith("ozagent-phase1-3-")
        assert "analyze-the-auth" in tid

    def test_without_phase(self):
        tid = spawn_oz.generate_task_id("x y z", 0)
        assert tid.startswith("ozagent-0-")

    def test_sanitizes_nonalnum(self):
        tid = spawn_oz.generate_task_id("foo@bar!baz", 0)
        assert "@" not in tid and "!" not in tid


class TestSpawnOzAgent:
    def test_success_returns_running_with_run_id(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            assert cmd[:3] == ["oz", "agent", "run-cloud"]
            assert "--environment" in cmd
            assert "UA17BXYZ" in cmd
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"run_id": "5972cca4-a410-42af-930a-e56bc23e07ac"}',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = spawn_oz.spawn_oz_agent(task="test", task_id="t1", environment="UA17BXYZ")
        assert r.status == "running"
        assert r.run_id == "5972cca4-a410-42af-930a-e56bc23e07ac"
        assert r.start_time is not None
        assert r.error is None

    def test_oz_cli_error_returns_failed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, stderr="Not authenticated")

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = spawn_oz.spawn_oz_agent(task="test", task_id="t1", environment="X")
        assert r.status == "failed"
        assert "Not authenticated" in r.error
        assert r.run_id == ""

    def test_missing_oz_cli_returns_failed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("oz")

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = spawn_oz.spawn_oz_agent(task="test", task_id="t1", environment="X")
        assert r.status == "failed"
        assert "oz" in r.error.lower()

    def test_unparseable_output_returns_failed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="Success but no run id here", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = spawn_oz.spawn_oz_agent(task="test", task_id="t1", environment="X")
        assert r.status == "failed"
        assert "Could not parse run_id" in r.error


class TestPollRun:
    def test_returns_parsed_json_on_success(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"status": "succeeded", "output": "all good"}',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        data = spawn_oz.poll_run("abc")
        assert data["status"] == "succeeded"

    def test_returns_unknown_on_nonzero(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="run not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        data = spawn_oz.poll_run("abc")
        assert data["status"] == "unknown"
        assert "run not found" in data["error"]

    def test_malformed_json_handled(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        data = spawn_oz.poll_run("abc")
        assert data["status"] == "unknown"
        assert "malformed JSON" in data["error"]


class TestWaitForRun:
    def test_terminal_state_captured(self, monkeypatch):
        r = spawn_oz.OzAgentResult(
            task_id="t1", task="x", run_id="abc", status="running", start_time=1.0
        )

        def fake_poll(run_id):
            return {
                "status": "succeeded",
                "output": "Created PR: https://github.com/foo/bar/pull/42 on branch feature/x",
            }

        monkeypatch.setattr(spawn_oz, "poll_run", fake_poll)
        monkeypatch.setattr(spawn_oz.time, "sleep", lambda _s: None)
        result = spawn_oz.wait_for_run(r, poll_sec=0.0, max_wait_sec=5)
        assert result.status == "succeeded"
        assert result.pr_url == "https://github.com/foo/bar/pull/42"
        assert result.branch == "feature/x"
        assert result.end_time is not None

    def test_non_terminal_times_out(self, monkeypatch):
        r = spawn_oz.OzAgentResult(
            task_id="t1", task="x", run_id="abc", status="running", start_time=1.0
        )

        def fake_poll(run_id):
            return {"status": "running"}

        monkeypatch.setattr(spawn_oz, "poll_run", fake_poll)
        monkeypatch.setattr(spawn_oz.time, "sleep", lambda _s: None)
        # Tight deadline
        result = spawn_oz.wait_for_run(r, poll_sec=0.0, max_wait_sec=0)
        assert result.status == "failed"
        assert "Timed out" in result.error


class TestToEnvelope:
    def test_succeeded_maps_to_ok(self):
        r = spawn_oz.OzAgentResult(
            task_id="t1", task="x", run_id="abc",
            status="succeeded", start_time=1.0, end_time=3.5,
            output="done", pr_url="https://github.com/a/b/pull/1",
        )
        env = r.to_envelope()
        assert env["schema_version"] == "1"
        assert env["status"] == "ok"
        assert env["task_id"] == "t1"
        assert env["data"]["run_id"] == "abc"
        assert env["data"]["pr_url"] == "https://github.com/a/b/pull/1"
        assert env["metrics"]["duration_seconds"] == pytest.approx(2.5)

    def test_failed_maps_to_failed(self):
        r = spawn_oz.OzAgentResult(
            task_id="t1", task="x", run_id="", status="failed", error="boom",
        )
        env = r.to_envelope()
        assert env["status"] == "failed"
        assert env["error"] == "boom"

    def test_running_maps_to_partial(self):
        r = spawn_oz.OzAgentResult(task_id="t1", task="x", run_id="abc", status="running")
        assert r.to_envelope()["status"] == "partial"


class TestCheckOzAvailable:
    def test_missing_cli(self, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        ok, msg = spawn_oz.check_oz_available()
        assert not ok
        assert "not found" in msg

    def test_version_check_ok(self, monkeypatch, tmp_path):
        # Fake an oz binary on PATH (executable)
        fake_oz = tmp_path / "oz"
        fake_oz.write_text("#!/bin/sh\necho oz-1.0\n")
        fake_oz.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="oz 1.0.0\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, msg = spawn_oz.check_oz_available()
        assert ok
        assert "1.0.0" in msg
