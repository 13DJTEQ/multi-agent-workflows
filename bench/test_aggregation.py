"""Benchmark aggregation hot paths (P0 baseline)."""
from __future__ import annotations

import pytest

from scripts.aggregate_results import (
    strategy_merge,
    strategy_concat,
    _rollup_metrics,
    merge_dicts,
)


@pytest.mark.parametrize("count", [10, 100, 1000, 10000])
def test_merge_strategy(benchmark, envelope_factory, count):
    """Baseline for merge strategy. Phase 7 P1-A target: streaming beats this."""
    data = envelope_factory.make(count)
    benchmark(strategy_merge, data)


@pytest.mark.parametrize("count", [10, 100, 1000])
def test_concat_strategy(benchmark, envelope_factory, count):
    data = envelope_factory.make(count)
    benchmark(strategy_concat, data)


@pytest.mark.parametrize("count", [10, 100, 1000, 10000])
def test_rollup_metrics(benchmark, envelope_factory, count):
    data = envelope_factory.make(count)
    benchmark(_rollup_metrics, data)


@pytest.mark.parametrize("count", [100, 1000])
def test_merge_dicts_last_policy(benchmark, envelope_factory, count):
    """The dict.update() fast path in merge_dicts (preserved from Pass 3 perf work)."""
    data = envelope_factory.make(count)
    benchmark(merge_dicts, data, "last")
