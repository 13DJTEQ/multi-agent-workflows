"""Tests for scripts/schema_validator.py + --validate-schema integration."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import schema_validator as sv


VALID = {
    "schema_version": "1",
    "status": "ok",
    "task_id": "t1",
    "data": {"result": "fine"},
    "metrics": {"duration_seconds": 1.5},
}


class TestBuiltinValidator:
    def test_valid_envelope(self):
        assert sv._builtin_validate(VALID) == []

    def test_missing_schema_version(self):
        errs = sv._builtin_validate({"status": "ok"})
        assert any("schema_version" in e for e in errs)

    def test_wrong_schema_version(self):
        errs = sv._builtin_validate({"schema_version": "2", "status": "ok"})
        assert any("schema_version must be '1'" in e for e in errs)

    def test_missing_status(self):
        errs = sv._builtin_validate({"schema_version": "1"})
        assert any("status" in e for e in errs)

    def test_invalid_status(self):
        errs = sv._builtin_validate({"schema_version": "1", "status": "ready"})
        assert any("status must be one of" in e for e in errs)

    def test_not_a_dict(self):
        errs = sv._builtin_validate(["list", "not", "dict"])
        assert any("JSON object" in e for e in errs)

    def test_bad_metrics_type(self):
        errs = sv._builtin_validate(
            {"schema_version": "1", "status": "ok", "metrics": "not-a-dict"}
        )
        assert any("metrics must be an object" in e for e in errs)

    def test_extra_fields_allowed(self):
        """additionalProperties: true — extra fields should not fail."""
        assert sv._builtin_validate({**VALID, "custom_field": 42, "nested": {"k": "v"}}) == []


class TestValidateEnvelope:
    def test_ok_result(self):
        r = sv.validate_envelope(VALID)
        assert r.ok
        assert r.errors == []
        assert bool(r) is True

    def test_bad_result(self):
        r = sv.validate_envelope({"status": "ok"})  # missing schema_version
        assert not r.ok
        assert r.errors
        assert bool(r) is False


class TestValidateFile:
    def test_valid_file(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps(VALID))
        r = sv.validate_file(p)
        assert r.ok
        assert r.path == p

    def test_invalid_json_file(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text("{not json}")
        r = sv.validate_file(p)
        assert not r.ok
        assert any("failed to load" in e for e in r.errors)

    def test_missing_file(self, tmp_path):
        r = sv.validate_file(tmp_path / "nope.json")
        assert not r.ok


class TestCliRunner:
    def test_cli_passes_on_valid(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps(VALID))
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.schema_validator", str(p), "--quiet"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0

    def test_cli_fails_on_invalid(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"status": "bogus"}))  # invalid
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.schema_validator", str(p)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 1


class TestAggregateValidateSchemaIntegration:
    def _mk(self, dirpath: Path, name: str, envelope: dict):
        d = dirpath / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps(envelope))

    def test_drops_failed_entries_from_merge(self, tmp_path):
        out = tmp_path / "outputs"
        self._mk(out, "a", {"schema_version": "1", "status": "ok", "data": {"x": 1}})
        self._mk(out, "b", {"schema_version": "1", "status": "failed", "error": "boom"})
        self._mk(out, "c", {"schema_version": "1", "status": "ok", "data": {"y": 2}})
        report = tmp_path / "report.json"

        proc = subprocess.run(
            [
                sys.executable, "-m", "scripts.aggregate_results",
                "--input-dir", str(out),
                "--output", str(report),
                "--strategy", "merge",
                "--validate-schema",
                "--include-stats",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(report.read_text())
        # Failed entry dropped -> merged data only has x and y
        assert payload["stats"]["status_breakdown"] == {"ok": 2, "failed": 1}

    def test_aborts_on_malformed_envelope(self, tmp_path):
        out = tmp_path / "outputs"
        self._mk(out, "a", {"schema_version": "1", "status": "ok", "data": {"x": 1}})
        self._mk(out, "bad", {"status": "ok"})  # missing schema_version
        report = tmp_path / "report.json"

        proc = subprocess.run(
            [
                sys.executable, "-m", "scripts.aggregate_results",
                "--input-dir", str(out),
                "--output", str(report),
                "--strategy", "merge",
                "--validate-schema",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode != 0
        assert "failed schema validation" in proc.stderr
