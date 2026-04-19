"""Shared pytest fixtures for multi-agent-workflows tests."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


@pytest.fixture
def mock_env(monkeypatch):
    """Set up mock environment variables."""
    monkeypatch.setenv("WARP_API_KEY", "test-api-key-12345")
    monkeypatch.setenv("HOME", "/tmp/test-home")


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def tmp_outputs(tmp_path):
    """Create a temporary outputs directory with sample results."""
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return outputs


@pytest.fixture
def sample_json_results(tmp_outputs):
    """Create sample JSON result files."""
    results = [
        {"module": "auth", "score": 85, "issues": ["weak password"]},
        {"module": "api", "score": 92, "issues": []},
        {"module": "db", "score": 78, "issues": ["missing indexes"]},
    ]

    paths = []
    for i, result in enumerate(results):
        agent_dir = tmp_outputs / f"agent-{i}"
        agent_dir.mkdir()
        result_file = agent_dir / "result.json"
        result_file.write_text(json.dumps(result))
        paths.append(result_file)

    return paths


@pytest.fixture
def sample_vote_results(tmp_outputs):
    """Create sample results for vote testing."""
    results = [
        {"decision": True, "confidence": 0.85},
        {"decision": True, "confidence": 0.92},
        {"decision": False, "confidence": 0.78},
    ]

    paths = []
    for i, result in enumerate(results):
        agent_dir = tmp_outputs / f"voter-{i}"
        agent_dir.mkdir()
        result_file = agent_dir / "result.json"
        result_file.write_text(json.dumps(result))
        paths.append(result_file)

    return paths


@pytest.fixture
def mock_subprocess_docker_success():
    """Mock subprocess for successful Docker operations."""

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        if cmd[0] == "docker":
            if cmd[1] == "run":
                mock_result.stdout = "abc123container456"
            elif cmd[1] == "wait":
                mock_result.stdout = "0"
            elif cmd[1] == "ps":
                mock_result.stdout = "agent-1-task\nagent-2-task"
            elif cmd[1] == "inspect":
                mock_result.stdout = "exited,0,2024-01-01T10:00:00Z,2024-01-01T10:05:00Z"

        return mock_result

    return _mock_run


@pytest.fixture
def mock_subprocess_docker_failure():
    """Mock subprocess for failed Docker operations."""

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Container failed to start"

        if cmd[0] == "docker":
            if cmd[1] == "wait":
                mock_result.stdout = "1"

        return mock_result

    return _mock_run
