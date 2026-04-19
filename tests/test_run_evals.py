"""Tests for run_evals.py."""

import json
from pathlib import Path

import pytest

import run_evals as re_mod


class TestAnalyzePromptForDecomposition:
    """Tests for analyze_prompt_for_decomposition."""

    def test_directories_mention(self):
        prompt = "Analyze modules: auth/, api/, db/, billing/"
        a = re_mod.analyze_prompt_for_decomposition(prompt)
        assert a["subtask_count"] >= 4
        assert a["parallel_potential"] is True
        assert "auth" in a["subtask_hints"]
        assert "billing" in a["subtask_hints"]

    def test_sharding_keywords(self):
        a = re_mod.analyze_prompt_for_decomposition("split this workload across shards")
        assert a["sharding_applicable"] is True

    def test_file_count_triggers_sharding(self):
        a = re_mod.analyze_prompt_for_decomposition("Process 500 test files quickly")
        assert a["sharding_applicable"] is True
        assert a["file_count"] == 500

    def test_version_enumeration(self):
        a = re_mod.analyze_prompt_for_decomposition("Test on Python 3.9, 3.10, 3.11, and 3.12")
        assert a["subtask_count"] >= 4
        assert a["parallel_potential"] is True
        assert a["version_enumeration"] == ["3.9", "3.10", "3.11", "3.12"]

    def test_phased_workflow_keywords(self):
        a = re_mod.analyze_prompt_for_decomposition(
            "First analyze the code, then run tests, finally synthesize results"
        )
        assert a["phased_workflow"] is True

    def test_k8s_backend_detected(self):
        a = re_mod.analyze_prompt_for_decomposition("Run across a Kubernetes cluster")
        assert a["suggested_backend"] == "k8s"

    def test_ci_backend_detected(self):
        a = re_mod.analyze_prompt_for_decomposition("Run via GitHub Actions CI pipeline")
        assert a["suggested_backend"] == "ci"

    def test_remote_backend_detected(self):
        a = re_mod.analyze_prompt_for_decomposition("Run over SSH on remote hosts")
        assert a["suggested_backend"] == "remote"

    def test_default_backend_docker(self):
        a = re_mod.analyze_prompt_for_decomposition("Just analyze this file")
        assert a["suggested_backend"] == "docker"

    def test_agent_count_extracted(self):
        a = re_mod.analyze_prompt_for_decomposition("Spawn 8 parallel agents to do X")
        assert a["explicit_agent_count"] == 8


class TestValidateDecomposition:
    """Tests for validate_decomposition."""

    def test_passes_when_subtask_count_matches(self):
        analysis = {
            "subtask_count": 4,
            "parallel_potential": True,
            "suggested_backend": "docker",
        }
        ok, errors = re_mod.validate_decomposition(analysis, ["decomposes into 4 subtasks"])
        assert ok is True
        assert errors == []

    def test_fails_when_k8s_expected_but_not_detected(self):
        analysis = {"suggested_backend": "docker"}
        ok, errors = re_mod.validate_decomposition(analysis, ["Use kubernetes backend"])
        assert ok is False
        assert any("K8s" in e or "k8s" in e.lower() for e in errors)

    def test_fails_when_phase_expected_but_not_detected(self):
        analysis = {"phased_workflow": False, "suggested_backend": "docker"}
        ok, errors = re_mod.validate_decomposition(analysis, ["Expected phase detection"])
        assert ok is False
        assert any("phased" in e.lower() for e in errors)

    def test_ci_backend_mismatch_is_error(self):
        analysis = {"suggested_backend": "docker"}
        ok, errors = re_mod.validate_decomposition(analysis, ["Use CI backend for this"])
        assert ok is False
        assert any("CI" in e for e in errors)


class TestEvaluateCaseDryRun:
    """Tests for evaluate_case_dry_run."""

    def _case(self, **overrides):
        case = {
            "id": "c1",
            "name": "example",
            "prompt": "Analyze modules: auth/, api/",
            "evaluation_criteria": {"backend_selection": "docker"},
            "expected_behavior": ["decomposes into 2 subtasks"],
            "tags": [],
        }
        case.update(overrides)
        return case

    def test_passing_case(self):
        case = self._case()
        analysis = re_mod.analyze_prompt_for_decomposition(case["prompt"])
        result = re_mod.evaluate_case_dry_run(case, analysis)
        assert result.case_id == "c1"
        assert result.score > 0
        assert result.passed is True
        assert result.criteria_results.get("decomposition_correct") is True

    def test_sharding_tag_requires_sharding(self):
        case = self._case(
            prompt="Analyze modules: auth/, api/",
            tags=["sharding"],
        )
        analysis = re_mod.analyze_prompt_for_decomposition(case["prompt"])
        result = re_mod.evaluate_case_dry_run(case, analysis)
        # prompt lacks sharding keywords so criterion should be False
        assert result.criteria_results.get("sharding_strategy") is False

    def test_score_threshold_enforced(self):
        # Build a case where no criteria pass.
        case = self._case(
            prompt="one liner with nothing useful",
            evaluation_criteria={"backend_selection": "kubernetes"},
            expected_behavior=["decomposes into 5 subtasks", "Use kubernetes backend"],
        )
        analysis = re_mod.analyze_prompt_for_decomposition(case["prompt"])
        result = re_mod.evaluate_case_dry_run(case, analysis)
        assert result.passed is False

    def test_phased_tag_triggers_phase_check(self):
        case = self._case(
            prompt="First do A, then do B",
            tags=["phased-execution"],
        )
        analysis = re_mod.analyze_prompt_for_decomposition(case["prompt"])
        result = re_mod.evaluate_case_dry_run(case, analysis)
        assert result.criteria_results.get("phases_identified") is True


class TestLoadEvalsAndRunDryRun:
    """Tests for load_evals / run_dry_run_eval end-to-end."""

    def _write_evals(self, tmp_path: Path, test_cases: list) -> Path:
        p = tmp_path / "evals.json"
        p.write_text(json.dumps({"test_cases": test_cases}))
        return p

    def test_load_evals_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            re_mod.load_evals(tmp_path / "no-such-file.json")

    def test_load_evals_ok(self, tmp_path):
        p = self._write_evals(tmp_path, [])
        data = re_mod.load_evals(p)
        assert data == {"test_cases": []}

    def test_run_dry_run_returns_results_for_each_case(self, tmp_path):
        cases = [
            {
                "id": "c1",
                "name": "A",
                "prompt": "Analyze modules: auth/, api/",
                "evaluation_criteria": {"backend_selection": "docker"},
                "expected_behavior": ["decomposes into 2 subtasks"],
                "tags": [],
            },
            {
                "id": "c2",
                "name": "B",
                "prompt": "Run across a Kubernetes cluster with 3 agents",
                "evaluation_criteria": {"backend_selection": "kubernetes"},
                "expected_behavior": ["decomposes into 3 subtasks", "Use kubernetes backend"],
                "tags": [],
            },
        ]
        results = re_mod.run_dry_run_eval({"test_cases": cases})
        assert len(results) == 2
        assert {r.case_id for r in results} == {"c1", "c2"}

    def test_run_dry_run_with_case_filter(self, tmp_path):
        cases = [
            {
                "id": "c1",
                "name": "A",
                "prompt": "whatever",
                "evaluation_criteria": {},
                "expected_behavior": [],
                "tags": [],
            },
            {
                "id": "c2",
                "name": "B",
                "prompt": "whatever",
                "evaluation_criteria": {},
                "expected_behavior": [],
                "tags": [],
            },
        ]
        results = re_mod.run_dry_run_eval({"test_cases": cases}, case_filter="c2")
        assert len(results) == 1
        assert results[0].case_id == "c2"


class TestPrintResults:
    """Light smoke tests for print_results exit code mapping."""

    def test_all_passed_returns_zero(self, capsys):
        results = [
            re_mod.EvalResult(case_id="c1", case_name="A", passed=True, score=1.0),
        ]
        rc = re_mod.print_results(results, verbose=False)
        assert rc == 0

    def test_any_failed_returns_one(self, capsys):
        results = [
            re_mod.EvalResult(case_id="c1", case_name="A", passed=True, score=1.0),
            re_mod.EvalResult(case_id="c2", case_name="B", passed=False, score=0.3),
        ]
        rc = re_mod.print_results(results, verbose=False)
        assert rc == 1
