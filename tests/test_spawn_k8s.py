"""Tests for spawn_k8s.py."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import spawn_k8s as sk


class TestGenerateJobName:
    """Tests for generate_job_name function."""

    def test_basic_job_name(self):
        result = sk.generate_job_name("Analyze auth module", 0)
        assert result.startswith("agent-0-")
        assert "analyze" in result.lower()

    def test_special_characters_removed(self):
        result = sk.generate_job_name("Task with $pecial! chars", 3)
        assert "$" not in result
        assert "!" not in result
        assert result.startswith("agent-3-")

    def test_spaces_and_underscores_become_dashes(self):
        result = sk.generate_job_name("multi word_task", 1)
        assert " " not in result
        assert "_" not in result
        assert "-" in result

    def test_length_bounded_by_k8s_limit(self):
        long_task = "A" * 200
        result = sk.generate_job_name(long_task, 7)
        # Script caps at 40 chars of task plus 'agent-{i}-' prefix; total <= 63.
        assert len(result) <= 63

    def test_does_not_end_with_dash(self):
        # K8s names must start/end with alphanumeric.
        result = sk.generate_job_name("trailing---", 0)
        assert not result.endswith("-")


class TestCreateJobManifest:
    """Tests for create_job_manifest function."""

    def _kwargs(self, **overrides):
        base = dict(
            task="Analyze X",
            job_name="agent-0-analyze-x",
            namespace="warp-agents",
            image="warpdotdev/dev-base:latest",
            secret_name="warp-api-key",
            pvc_name=None,
            memory_request="2Gi",
            memory_limit="4Gi",
            cpu_request="1",
            cpu_limit="2",
            share="team",
        )
        base.update(overrides)
        return base

    def test_basic_manifest_shape(self):
        m = sk.create_job_manifest(**self._kwargs())
        assert m["apiVersion"] == "batch/v1"
        assert m["kind"] == "Job"
        assert m["metadata"]["name"] == "agent-0-analyze-x"
        assert m["metadata"]["namespace"] == "warp-agents"
        assert m["metadata"]["labels"]["app"] == "warp-agent"
        assert m["metadata"]["labels"]["task-id"] == "agent-0-analyze-x"

    def test_restart_policy_never(self):
        m = sk.create_job_manifest(**self._kwargs())
        assert m["spec"]["template"]["spec"]["restartPolicy"] == "Never"

    def test_ttl_and_backoff_set(self):
        m = sk.create_job_manifest(**self._kwargs())
        assert m["spec"]["backoffLimit"] == 3
        assert m["spec"]["ttlSecondsAfterFinished"] == 3600

    def test_no_pvc_excludes_workspace_volume(self):
        m = sk.create_job_manifest(**self._kwargs(pvc_name=None))
        volumes = m["spec"]["template"]["spec"]["volumes"]
        assert all(v["name"] != "workspace" for v in volumes)
        # Only the output emptyDir should be present.
        assert any(v["name"] == "output" for v in volumes)
        # Container workingDir falls back to /tmp.
        container = m["spec"]["template"]["spec"]["containers"][0]
        assert container["workingDir"] == "/tmp"

    def test_pvc_includes_workspace_volume(self):
        m = sk.create_job_manifest(**self._kwargs(pvc_name="scratch"))
        volumes = m["spec"]["template"]["spec"]["volumes"]
        workspace = [v for v in volumes if v["name"] == "workspace"]
        assert len(workspace) == 1
        assert workspace[0]["persistentVolumeClaim"]["claimName"] == "scratch"
        container = m["spec"]["template"]["spec"]["containers"][0]
        assert container["workingDir"] == "/workspace"

    def test_env_contains_secret_ref_and_task_id(self):
        m = sk.create_job_manifest(**self._kwargs(job_name="agent-9-x", secret_name="my-secret"))
        env = m["spec"]["template"]["spec"]["containers"][0]["env"]
        names = {e["name"]: e for e in env}
        assert "WARP_API_KEY" in names
        assert names["WARP_API_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "my-secret"
        assert names["TASK_ID"]["value"] == "agent-9-x"
        assert names["OUTPUT_DIR"]["value"] == "/output"

    def test_resource_limits_propagated(self):
        m = sk.create_job_manifest(
            **self._kwargs(
                memory_request="512Mi",
                memory_limit="1Gi",
                cpu_request="500m",
                cpu_limit="1500m",
            )
        )
        res = m["spec"]["template"]["spec"]["containers"][0]["resources"]
        assert res["requests"] == {"memory": "512Mi", "cpu": "500m"}
        assert res["limits"] == {"memory": "1Gi", "cpu": "1500m"}

    def test_args_embed_task_and_share(self):
        m = sk.create_job_manifest(**self._kwargs(task="Do thing", share="public"))
        args = m["spec"]["template"]["spec"]["containers"][0]["args"]
        assert "--share" in args
        assert "public" in args
        joined = " ".join(args)
        assert "Do thing" in joined
        assert "/output/result.json" in joined


class TestApplyManifest:
    """Tests for apply_manifest (subprocess boundary)."""

    @patch("spawn_k8s.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="job.batch/agent-0 created", stderr="", returncode=0)
        ok, out = sk.apply_manifest({"kind": "Job"})
        assert ok is True
        assert "created" in out
        # Validate kubectl was invoked with stdin manifest.
        args, kwargs = mock_run.call_args
        assert args[0][:3] == ["kubectl", "apply", "-f"]
        assert kwargs.get("input")  # yaml manifest piped on stdin

    @patch("spawn_k8s.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl", stderr="unauthorized")
        ok, err = sk.apply_manifest({"kind": "Job"})
        assert ok is False
        assert "unauthorized" in err


class TestGetJobStatus:
    """Tests for get_job_status's jsonpath output parsing."""

    @patch("spawn_k8s.subprocess.run")
    def test_completed(self, mock_run):
        # jsonpath output: "<complete>,<failed>,<active>"
        mock_run.return_value = MagicMock(stdout="True,,", stderr="")
        assert sk.get_job_status("j", "ns") == "completed"

    @patch("spawn_k8s.subprocess.run")
    def test_failed(self, mock_run):
        mock_run.return_value = MagicMock(stdout=",True,", stderr="")
        assert sk.get_job_status("j", "ns") == "failed"

    @patch("spawn_k8s.subprocess.run")
    def test_running(self, mock_run):
        mock_run.return_value = MagicMock(stdout=",,1", stderr="")
        assert sk.get_job_status("j", "ns") == "running"

    @patch("spawn_k8s.subprocess.run")
    def test_pending_when_all_empty(self, mock_run):
        mock_run.return_value = MagicMock(stdout=",,", stderr="")
        assert sk.get_job_status("j", "ns") == "pending"

    @patch("spawn_k8s.subprocess.run")
    def test_kubectl_error_returns_unknown(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl")
        assert sk.get_job_status("j", "ns") == "unknown"


class TestWaitForJob:
    """Tests for wait_for_job polling."""

    @patch("spawn_k8s.time.sleep", lambda _x: None)
    @patch("spawn_k8s.get_job_status")
    def test_completes_quickly(self, mock_status):
        mock_status.side_effect = ["pending", "running", "completed"]
        assert sk.wait_for_job("j", "ns", timeout=60) == "completed"

    @patch("spawn_k8s.time.sleep", lambda _x: None)
    @patch("spawn_k8s.get_job_status")
    def test_returns_failed(self, mock_status):
        mock_status.side_effect = ["running", "failed"]
        assert sk.wait_for_job("j", "ns", timeout=60) == "failed"

    @patch("spawn_k8s.time.sleep", lambda _x: None)
    @patch("spawn_k8s.get_job_status", return_value="running")
    def test_timeout(self, _mock_status):
        # timeout=0 means while loop exits immediately -> 'timeout'
        assert sk.wait_for_job("j", "ns", timeout=0) == "timeout"


class TestDeleteJob:
    @patch("spawn_k8s.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert sk.delete_job("j", "ns") is True

    @patch("spawn_k8s.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "kubectl")
        assert sk.delete_job("j", "ns") is False


class TestGetJobLogs:
    @patch("spawn_k8s.subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(stdout="hello world", stderr="")
        assert sk.get_job_logs("j", "ns") == "hello world"

    @patch("spawn_k8s.subprocess.run")
    def test_handles_exception(self, mock_run):
        mock_run.side_effect = RuntimeError("boom")
        assert sk.get_job_logs("j", "ns") == ""


class TestJobResult:
    def test_defaults(self):
        r = sk.JobResult(task_id="t", task="x", job_name="j", status="pending")
        assert r.error is None
        assert r.status == "pending"
