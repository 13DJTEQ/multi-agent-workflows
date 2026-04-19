"""Tests for spawn_docker.py."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import module under test
import spawn_docker as sd


class TestGenerateTaskId:
    """Tests for generate_task_id function."""

    def test_basic_task_id(self):
        result = sd.generate_task_id("Analyze auth module", 0)
        assert result.startswith("agent-0-")
        assert "analyze" in result.lower()
        assert len(result) <= 45  # Max length check

    def test_with_phase(self):
        result = sd.generate_task_id("Task", 0, phase="1")
        assert result.startswith("agent-1-0-")

    def test_special_characters_removed(self):
        result = sd.generate_task_id("Task with $pecial! chars", 0)
        assert "$" not in result
        assert "!" not in result

    def test_spaces_converted_to_dashes(self):
        result = sd.generate_task_id("multi word task", 0)
        assert " " not in result
        assert "-" in result

    def test_truncation(self):
        long_task = "A" * 100
        result = sd.generate_task_id(long_task, 0)
        assert len(result) <= 45


class TestCircuitBreaker:
    """Tests for check_circuit_breaker function."""

    def test_no_trigger_below_min_samples(self):
        results = [
            sd.AgentResult("t1", "task", "c1", "failed"),
            sd.AgentResult("t2", "task", "c2", "failed"),
        ]
        # 2 samples < min_samples of 3
        assert sd.check_circuit_breaker(results, 0.3) is False

    def test_triggers_above_threshold(self):
        results = [
            sd.AgentResult("t1", "task", "c1", "failed"),
            sd.AgentResult("t2", "task", "c2", "failed"),
            sd.AgentResult("t3", "task", "c3", "completed"),
        ]
        # 66% failure > 50% threshold
        assert sd.check_circuit_breaker(results, 0.5) is True

    def test_no_trigger_at_threshold(self):
        results = [
            sd.AgentResult("t1", "task", "c1", "failed"),
            sd.AgentResult("t2", "task", "c2", "completed"),
            sd.AgentResult("t3", "task", "c3", "completed"),
        ]
        # 33% failure <= 50% threshold
        assert sd.check_circuit_breaker(results, 0.5) is False

    def test_all_success(self):
        results = [
            sd.AgentResult("t1", "task", "c1", "completed"),
            sd.AgentResult("t2", "task", "c2", "completed"),
            sd.AgentResult("t3", "task", "c3", "completed"),
        ]
        assert sd.check_circuit_breaker(results, 0.3) is False


class TestBackoffCalculation:
    """Tests for calculate_backoff function."""

    def test_exponential_growth(self):
        b0 = sd.calculate_backoff(0, base_delay=2.0, max_delay=60.0)
        b1 = sd.calculate_backoff(1, base_delay=2.0, max_delay=60.0)
        b2 = sd.calculate_backoff(2, base_delay=2.0, max_delay=60.0)
        
        # Should grow exponentially (with jitter)
        assert b0 < b1 < b2

    def test_respects_max_delay(self):
        result = sd.calculate_backoff(10, base_delay=2.0, max_delay=60.0)
        # With 10% jitter, max should be 66
        assert result <= 66.0

    def test_jitter_applied(self):
        # Run multiple times to check jitter varies
        results = [sd.calculate_backoff(1, base_delay=2.0, max_delay=60.0) for _ in range(10)]
        # Not all results should be identical due to jitter
        assert len(set(results)) > 1


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_duration_calculation(self):
        result = sd.AgentResult(
            task_id="t1",
            task="task",
            container_id="c1",
            status="completed",
            start_time=100.0,
            end_time=105.5,
        )
        assert result.duration_seconds == 5.5

    def test_duration_none_when_incomplete(self):
        result = sd.AgentResult(
            task_id="t1",
            task="task",
            container_id="c1",
            status="running",
            start_time=100.0,
        )
        assert result.duration_seconds is None


class TestSpawnContainer:
    """Tests for spawn_container function (mocked)."""

    @patch("spawn_docker.subprocess.run")
    def test_successful_spawn(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stdout="container123\n",
            stderr="",
            returncode=0,
        )
        
        result = sd.spawn_container(
            task="Test task",
            task_id="agent-0-test",
            image="test-image",
            workspace=tmp_path,
            output_dir=tmp_path / "outputs",
            api_key="test-key",
            memory="4g",
            cpus="2",
            network=None,
            share="team",
            extra_env={},
        )
        
        assert result.status == "running"
        assert result.container_id == "container123"
        assert mock_run.called

    @patch("spawn_docker.subprocess.run")
    def test_failed_spawn(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "docker", stderr="Image not found"
        )
        
        result = sd.spawn_container(
            task="Test task",
            task_id="agent-0-test",
            image="bad-image",
            workspace=tmp_path,
            output_dir=tmp_path / "outputs",
            api_key="test-key",
            memory="4g",
            cpus="2",
            network=None,
            share="team",
            extra_env={},
        )
        
        assert result.status == "failed"
        assert result.container_id == ""


class TestWaitForContainer:
    """Tests for wait_for_container function."""

    @patch("spawn_docker.subprocess.run")
    def test_successful_completion(self, mock_run):
        mock_run.return_value = MagicMock(stdout="0\n", stderr="")
        
        exit_code, error = sd.wait_for_container("test-container", timeout=60)
        
        assert exit_code == 0
        assert error == ""

    @patch("spawn_docker.subprocess.run")
    def test_failed_container(self, mock_run):
        mock_run.return_value = MagicMock(stdout="1\n", stderr="")
        
        exit_code, error = sd.wait_for_container("test-container", timeout=60)
        
        assert exit_code == 1

    @patch("spawn_docker.subprocess.run")
    def test_timeout_handling(self, mock_run):
        # First call (docker wait) times out, second call (docker stop) succeeds
        mock_run.side_effect = [
            subprocess.TimeoutExpired("docker wait", 60),
            MagicMock(stdout="", stderr=""),
        ]
        
        exit_code, error = sd.wait_for_container("test-container", timeout=60)
        
        assert exit_code == -1
        assert "Timeout" in error


class TestValidateTasksFile:
    """Tests for validate_tasks_file function."""

    def test_valid_file(self, tmp_path):
        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Task 1\nTask 2\n# Comment\nTask 3")
        
        result = sd.validate_tasks_file(tasks_file)
        
        assert result == ["Task 1", "Task 2", "Task 3"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(SystemExit):
            sd.validate_tasks_file(tmp_path / "nonexistent.txt")

    def test_empty_file(self, tmp_path):
        tasks_file = tmp_path / "empty.txt"
        tasks_file.write_text("# Only comments\n\n")
        
        with pytest.raises(SystemExit):
            sd.validate_tasks_file(tasks_file)
