"""Benchmark circuit-breaker cost as N grows.

The list variant (P0 baseline) is O(N). The counter variant (P1-C) is O(1)
per call — its cost should be flat regardless of `total`.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts._common import check_circuit_breaker, check_circuit_breaker_counters


@dataclass
class _R:
    status: str


@pytest.mark.parametrize("count", [10, 100, 1000, 10000])
def test_check_circuit_breaker_list_variant(benchmark, count):
    results = [_R("failed") if i % 4 == 0 else _R("ok") for i in range(count)]
    benchmark(check_circuit_breaker, results, 0.3)


@pytest.mark.parametrize("count", [10, 100, 1000, 10000])
def test_check_circuit_breaker_counters_variant(benchmark, count):
    """P1-C: cost should be flat regardless of count."""
    failed = count // 4
    benchmark(check_circuit_breaker_counters, failed, count, 0.3)
