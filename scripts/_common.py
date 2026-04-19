"""Shared helpers for multi-agent-workflows spawn backends.

Kept module-private (leading underscore) so it's clear this is internal
plumbing, not part of the public script surface. Each spawn_* script
imports from here to avoid duplicating fault-tolerance logic.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Protocol


class _HasStatus(Protocol):
    """Duck-typed result with a `status` attribute (ok, partial, failed, etc)."""

    status: str


def calculate_backoff(retry: int, base_delay: float = 2.0, max_delay: float = 60.0) -> float:
    """Exponential backoff with 10% jitter. retry is 0-indexed."""
    delay = min(base_delay * (2 ** retry), max_delay)
    jitter = delay * 0.1 * random.random()
    return delay + jitter


def check_circuit_breaker(
    results: list[_HasStatus],
    threshold: float,
    min_samples: int = 3,
) -> bool:
    """Return True if the failure rate over `results` exceeds `threshold`.

    `min_samples` guards against early false positives when only 1-2 results
    have come back.

    Note: O(N) per call because it rescans the list. Spawn loops at scale
    should prefer `check_circuit_breaker_counters(failed, total, threshold,
    min_samples)` which is O(1). Kept here for backward compatibility with
    existing callers and tests.
    """
    total = len(results)
    if total < min_samples:
        return False
    failed = sum(1 for r in results if r.status == "failed")
    return (failed / total) > threshold


def check_circuit_breaker_counters(
    failed: int,
    total: int,
    threshold: float,
    min_samples: int = 3,
) -> bool:
    """O(1) circuit breaker check for callers that already track counters.

    Callers are expected to increment ``failed`` and ``total`` as each result
    arrives and pass both to this helper. Semantics match
    :func:`check_circuit_breaker` exactly.
    """
    if total < min_samples:
        return False
    return (failed / total) > threshold


def validate_tasks_file(path: Path) -> list[str]:
    """Load tasks from a file; blank lines and `# ...` comments are skipped.

    Exits with status 1 on missing file or empty task list — this is a CLI
    helper, not library code.
    """
    if not path.exists():
        print(f"Error: Tasks file not found: {path}", file=sys.stderr)
        sys.exit(1)
    tasks = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not tasks:
        print(f"Error: No tasks found in {path}", file=sys.stderr)
        sys.exit(1)
    return tasks
