"""Tests for scripts/_common.py."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts import _common


@dataclass
class _R:
    status: str


class TestCalculateBackoff:
    def test_grows_exponentially(self):
        a = _common.calculate_backoff(0, base_delay=1.0, max_delay=100.0)
        b = _common.calculate_backoff(3, base_delay=1.0, max_delay=100.0)
        # 1s baseline grows to ~8s at retry=3 (plus <10% jitter)
        assert 0.9 <= a <= 1.2
        assert 7.9 <= b <= 9.0

    def test_clamps_to_max(self):
        d = _common.calculate_backoff(20, base_delay=1.0, max_delay=5.0)
        assert d <= 5.5  # max + jitter


class TestCheckCircuitBreaker:
    def test_under_min_samples_returns_false(self):
        assert not _common.check_circuit_breaker([_R("failed"), _R("failed")], 0.3)

    def test_at_min_samples_trips(self):
        rs = [_R("failed"), _R("failed"), _R("failed")]
        assert _common.check_circuit_breaker(rs, 0.3)

    def test_below_threshold_does_not_trip(self):
        rs = [_R("failed"), _R("ok"), _R("ok")]
        assert not _common.check_circuit_breaker(rs, 0.5)

    def test_custom_min_samples(self):
        assert not _common.check_circuit_breaker([_R("failed")] * 4, 0.5, min_samples=5)
        assert _common.check_circuit_breaker([_R("failed")] * 5, 0.5, min_samples=5)


class TestValidateTasksFile:
    def test_loads_non_empty_lines(self, tmp_path):
        f = tmp_path / "tasks.txt"
        f.write_text("task one\n\n# a comment\ntask two\n")
        assert _common.validate_tasks_file(f) == ["task one", "task two"]

    def test_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _common.validate_tasks_file(tmp_path / "missing.txt")
        assert exc.value.code == 1

    def test_empty_file_exits(self, tmp_path):
        f = tmp_path / "tasks.txt"
        f.write_text("\n# only comments\n\n")
        with pytest.raises(SystemExit) as exc:
            _common.validate_tasks_file(f)
        assert exc.value.code == 1
