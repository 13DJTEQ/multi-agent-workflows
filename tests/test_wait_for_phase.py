"""Tests for wait_for_phase.py."""

import json
from unittest.mock import MagicMock, patch

import wait_for_phase as wfp


class TestStatus:
    """Tests for Status enum."""

    def test_status_values(self):
        assert wfp.Status.PENDING.value == "pending"
        assert wfp.Status.RUNNING.value == "running"
        assert wfp.Status.COMPLETED.value == "completed"
        assert wfp.Status.FAILED.value == "failed"


class TestAgentStatus:
    """Tests for AgentStatus dataclass."""

    def test_basic_creation(self):
        status = wfp.AgentStatus(
            agent_id="test-1",
            status=wfp.Status.COMPLETED,
            exit_code=0,
        )
        assert status.agent_id == "test-1"
        assert status.status == wfp.Status.COMPLETED
        assert status.exit_code == 0


class TestGetDockerAgents:
    """Tests for get_docker_agents function."""

    @patch("wait_for_phase.subprocess.run")
    def test_returns_agent_list(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="agent-1-task\nagent-1-other\n",
            returncode=0,
        )

        agents = wfp.get_docker_agents("1")

        assert agents == ["agent-1-task", "agent-1-other"]

    @patch("wait_for_phase.subprocess.run")
    def test_handles_empty_result(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        agents = wfp.get_docker_agents("nonexistent")

        assert agents == []

    @patch("wait_for_phase.subprocess.run")
    def test_handles_exception(self, mock_run):
        mock_run.side_effect = Exception("Docker not running")

        agents = wfp.get_docker_agents("1")

        assert agents == []


class TestGetDockerStatus:
    """Tests for get_docker_status function."""

    @patch("wait_for_phase.subprocess.run")
    def test_running_container(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="running,0,2024-01-01T10:00:00Z,0001-01-01T00:00:00Z",
            returncode=0,
        )

        status = wfp.get_docker_status("test-container")

        assert status.status == wfp.Status.RUNNING
        assert status.exit_code is None

    @patch("wait_for_phase.subprocess.run")
    def test_completed_container(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="exited,0,2024-01-01T10:00:00Z,2024-01-01T10:05:00Z",
            returncode=0,
        )

        status = wfp.get_docker_status("test-container")

        assert status.status == wfp.Status.COMPLETED
        assert status.exit_code == 0

    @patch("wait_for_phase.subprocess.run")
    def test_failed_container(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="exited,1,2024-01-01T10:00:00Z,2024-01-01T10:05:00Z",
            returncode=0,
        )

        status = wfp.get_docker_status("test-container")

        assert status.status == wfp.Status.FAILED
        assert status.exit_code == 1

    @patch("wait_for_phase.subprocess.run")
    def test_created_container(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="created,0,0001-01-01T00:00:00Z,0001-01-01T00:00:00Z",
            returncode=0,
        )

        status = wfp.get_docker_status("test-container")

        assert status.status == wfp.Status.PENDING


class TestGetK8sJobs:
    """Tests for get_k8s_jobs function."""

    @patch("wait_for_phase.subprocess.run")
    def test_returns_jobs_by_label(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="job-phase-1-a job-phase-1-b",
            returncode=0,
        )

        jobs = wfp.get_k8s_jobs("1", "warp-agents")

        assert len(jobs) == 2

    @patch("wait_for_phase.subprocess.run")
    def test_fallback_to_name_pattern(self, mock_run):
        # First call (by label) fails, second (all jobs) succeeds
        mock_run.side_effect = [
            Exception("Label not found"),
            MagicMock(stdout="agent-1-task other-job phase-1-work", returncode=0),
        ]

        jobs = wfp.get_k8s_jobs("1", "warp-agents")

        # Should match jobs containing "-1-" or "phase-1"
        assert "phase-1-work" in jobs


class TestGetK8sStatus:
    """Tests for get_k8s_status function."""

    @patch("wait_for_phase.subprocess.run")
    def test_succeeded_job(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(
                {
                    "status": {
                        "succeeded": 1,
                        "active": 0,
                        "failed": 0,
                        "startTime": "2024-01-01T10:00:00Z",
                        "completionTime": "2024-01-01T10:05:00Z",
                    }
                }
            ),
            returncode=0,
        )

        status = wfp.get_k8s_status("test-job", "default")

        assert status.status == wfp.Status.COMPLETED
        assert status.exit_code == 0

    @patch("wait_for_phase.subprocess.run")
    def test_failed_job(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"status": {"succeeded": 0, "active": 0, "failed": 1}}),
            returncode=0,
        )

        status = wfp.get_k8s_status("test-job", "default")

        assert status.status == wfp.Status.FAILED
        assert status.exit_code == 1

    @patch("wait_for_phase.subprocess.run")
    def test_running_job(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"status": {"succeeded": 0, "active": 1, "failed": 0}}),
            returncode=0,
        )

        status = wfp.get_k8s_status("test-job", "default")

        assert status.status == wfp.Status.RUNNING


class TestWaitForAgents:
    """Tests for wait_for_agents function."""

    def test_all_complete_immediately(self):
        def mock_status(agent):
            return wfp.AgentStatus(agent, wfp.Status.COMPLETED, exit_code=0)

        statuses, success = wfp.wait_for_agents(
            agents=["a1", "a2"],
            get_status_fn=mock_status,
            timeout=10,
            poll_interval=1,
            fail_fast=False,
        )

        assert success is True
        assert len(statuses) == 2
        assert all(s.status == wfp.Status.COMPLETED for s in statuses)

    def test_fail_fast_on_failure(self):
        call_count = [0]

        def mock_status(agent):
            call_count[0] += 1
            if agent == "a1":
                return wfp.AgentStatus(agent, wfp.Status.FAILED, exit_code=1)
            return wfp.AgentStatus(agent, wfp.Status.RUNNING)

        statuses, success = wfp.wait_for_agents(
            agents=["a1", "a2"],
            get_status_fn=mock_status,
            timeout=10,
            poll_interval=0.1,
            fail_fast=True,
        )

        assert success is False
        # Should have checked status of failed agent

    def test_mixed_results(self):
        statuses_map = {
            "a1": wfp.AgentStatus("a1", wfp.Status.COMPLETED, exit_code=0),
            "a2": wfp.AgentStatus("a2", wfp.Status.FAILED, exit_code=1),
            "a3": wfp.AgentStatus("a3", wfp.Status.COMPLETED, exit_code=0),
        }

        statuses, success = wfp.wait_for_agents(
            agents=["a1", "a2", "a3"],
            get_status_fn=lambda a: statuses_map[a],
            timeout=10,
            poll_interval=1,
            fail_fast=False,
        )

        assert success is True  # All agents reached terminal state
        completed = sum(1 for s in statuses if s.status == wfp.Status.COMPLETED)
        assert completed == 2


class TestBackend:
    """Tests for Backend enum."""

    def test_docker_value(self):
        assert wfp.Backend.DOCKER.value == "docker"

    def test_k8s_value(self):
        assert wfp.Backend.KUBERNETES.value == "k8s"
