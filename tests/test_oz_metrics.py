"""Tests for P1-C: Oz cost/metrics surface.

Covers:
- `_extract_metrics_from_oz` across response shape variants
- `wait_for_run` populates OzAgentResult.tokens_used/cost_usd/model
- `to_envelope` includes metrics in result-schema envelope
- `_rollup_metrics` sums totals and builds per-model breakdown
"""

from __future__ import annotations

from scripts import aggregate_results, spawn_oz


class TestExtractMetricsFromOz:
    def test_usage_nested_object(self):
        payload = {
            "status": "succeeded",
            "usage": {
                "tokens_used": 1234,
                "cost_usd": 0.0456,
                "model": "claude-opus-4",
            },
        }
        out = spawn_oz._extract_metrics_from_oz(payload)
        assert out == {"tokens_used": 1234, "cost_usd": 0.0456, "model": "claude-opus-4"}

    def test_metrics_nested_alternative(self):
        payload = {"metrics": {"total_tokens": 500, "cost": 0.01, "model": "gpt-4"}}
        out = spawn_oz._extract_metrics_from_oz(payload)
        assert out["tokens_used"] == 500
        assert out["cost_usd"] == 0.01
        assert out["model"] == "gpt-4"

    def test_flat_fallback(self):
        payload = {"tokens_used": 100, "cost_usd": 0.001, "model": "sonnet"}
        out = spawn_oz._extract_metrics_from_oz(payload)
        assert out == {"tokens_used": 100, "cost_usd": 0.001, "model": "sonnet"}

    def test_empty_payload(self):
        assert spawn_oz._extract_metrics_from_oz({}) == {}

    def test_partial_usage(self):
        payload = {"usage": {"tokens_used": 42}}
        out = spawn_oz._extract_metrics_from_oz(payload)
        assert out == {"tokens_used": 42}

    def test_bad_types_ignored(self):
        payload = {"usage": {"tokens_used": "oops", "cost_usd": "nan", "model": 123}}
        out = spawn_oz._extract_metrics_from_oz(payload)
        # model requires str; tokens/cost require numeric — all filtered out
        assert out == {}


class TestWaitForRunPopulatesMetrics:
    def test_populates_fields_on_success(self, monkeypatch):
        r = spawn_oz.OzAgentResult(task_id="t1", task="x", run_id="abc", status="running", start_time=1.0)

        def fake_poll(run_id):
            return {
                "status": "succeeded",
                "output": "",
                "usage": {"tokens_used": 777, "cost_usd": 0.12, "model": "claude-opus-4"},
            }

        monkeypatch.setattr(spawn_oz, "poll_run", fake_poll)
        monkeypatch.setattr(spawn_oz.time, "sleep", lambda _s: None)
        result = spawn_oz.wait_for_run(r, poll_sec=0.0, max_wait_sec=5)
        assert result.tokens_used == 777
        assert result.cost_usd == 0.12
        assert result.model == "claude-opus-4"


class TestEnvelopeIncludesMetrics:
    def test_envelope_surfaces_metrics(self):
        r = spawn_oz.OzAgentResult(
            task_id="t1",
            task="x",
            run_id="abc",
            status="succeeded",
            start_time=1.0,
            end_time=3.0,
            tokens_used=100,
            cost_usd=0.05,
            model="sonnet",
        )
        env = r.to_envelope()
        assert env["metrics"]["tokens_used"] == 100
        assert env["metrics"]["cost_usd"] == 0.05
        assert env["metrics"]["model"] == "sonnet"
        assert env["metrics"]["duration_seconds"] == 2.0

    def test_envelope_omits_metrics_when_empty(self):
        r = spawn_oz.OzAgentResult(task_id="t1", task="x", run_id="abc", status="running")
        env = r.to_envelope()
        assert "metrics" not in env


class TestRollupMetrics:
    def test_empty_input_returns_empty(self):
        assert aggregate_results._rollup_metrics([]) == {}

    def test_non_envelope_inputs_ignored(self):
        assert aggregate_results._rollup_metrics(["not a dict", {"no_metrics": 1}]) == {}

    def test_sums_tokens_and_cost(self):
        results = [
            {"metrics": {"tokens_used": 100, "cost_usd": 0.01, "model": "gpt-4"}},
            {"metrics": {"tokens_used": 250, "cost_usd": 0.03, "model": "gpt-4"}},
        ]
        rollup = aggregate_results._rollup_metrics(results)
        assert rollup["total_tokens"] == 350
        assert rollup["total_cost_usd"] == 0.04
        assert "gpt-4" in rollup["per_model"]
        assert rollup["per_model"]["gpt-4"]["count"] == 2
        assert rollup["per_model"]["gpt-4"]["tokens_used"] == 350

    def test_per_model_breakdown(self):
        results = [
            {"metrics": {"tokens_used": 100, "cost_usd": 0.01, "model": "claude"}},
            {"metrics": {"tokens_used": 200, "cost_usd": 0.02, "model": "gpt-4"}},
            {"metrics": {"tokens_used": 50, "cost_usd": 0.005, "model": "claude"}},
        ]
        rollup = aggregate_results._rollup_metrics(results)
        assert rollup["per_model"]["claude"]["count"] == 2
        assert rollup["per_model"]["claude"]["tokens_used"] == 150
        assert rollup["per_model"]["gpt-4"]["count"] == 1
        assert rollup["per_model"]["gpt-4"]["tokens_used"] == 200

    def test_duration_only_metrics(self):
        """When only duration is present (no tokens/cost), still surface the rollup."""
        results = [{"metrics": {"duration_seconds": 1.5}}, {"metrics": {"duration_seconds": 2.5}}]
        rollup = aggregate_results._rollup_metrics(results)
        assert rollup["total_duration_seconds"] == 4.0
        # No total_tokens / total_cost should appear
        assert "total_tokens" not in rollup
        assert "total_cost_usd" not in rollup
