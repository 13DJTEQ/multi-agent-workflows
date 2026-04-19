"""Benchmark circuit-breaker cost as N grows (P0 baseline for P1-C).

Current impl is O(N) per call; P1-C should make it O(1).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts._common import check_circuit_breaker


@dataclass
class _R:
    status: str


@pytest.mark.parametrize("count", [10, 100, 1000, 10000])
def test_check_circuit_breaker_list_variant(benchmark, count):
    results = [_R("failed") if i % 4 == 0 else _R("ok") for i in range(count)]
    benchmark(check_circuit_breaker, results, 0.3)
