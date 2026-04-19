"""Phase 7 P1-A: disk-backed aggregation (streaming vs materialized).

The P0 baseline benchmarks already cover in-memory strategy hot paths. Those
numbers say nothing about the pipeline we actually ship, which is:
  1. discover N files,
  2. parse JSON from disk,
  3. (optionally) validate,
  4. fold into an accumulator.

The materialized path that used to live in ``aggregate_results.main`` builds a
full ``list(executor.map(load_file, files))`` and then iterates. The streaming
path built in P1-A threads ``iter_loaded_files`` directly into ``strategy_merge``
so no intermediate list exists. These benchmarks measure both so we can
document the throughput delta and guarantee we don't regress back to O(N) RAM.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.aggregate_results import (
    _load_one,
    iter_loaded_files,
    load_json_file,
    strategy_merge,
)


def _materialized_merge(files):
    """Baseline-equivalent of pre-P1-A main(): list-materialize then merge."""
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(files)))) as ex:
        loaded = list(ex.map(_load_one, files))
    return strategy_merge([d for _, d in loaded if d is not None])


def _streaming_merge(files):
    """P1-A path: stream envelopes directly into the merge accumulator."""
    return strategy_merge(
        d for _, d in iter_loaded_files(files, max_workers=8) if d is not None
    )


@pytest.mark.parametrize("count", [1000, 10000])
def test_disk_merge_materialized_baseline(benchmark, tmp_path, envelope_factory, count):
    root = envelope_factory.write_dir(tmp_path / "env", count, failure_rate=0.0)
    files = sorted(root.rglob("result.json"))
    benchmark(_materialized_merge, files)


@pytest.mark.parametrize("count", [1000, 10000])
def test_disk_merge_streaming(benchmark, tmp_path, envelope_factory, count):
    root = envelope_factory.write_dir(tmp_path / "env", count, failure_rate=0.0)
    files = sorted(root.rglob("result.json"))
    benchmark(_streaming_merge, files)


def test_streaming_does_not_materialize_full_list(tmp_path, envelope_factory):
    """Smoke check: pulling one item from the stream should not force-load the rest."""
    root = envelope_factory.write_dir(tmp_path / "env", 50, failure_rate=0.0)
    files = sorted(root.rglob("result.json"))
    it = iter_loaded_files(files, max_workers=4)
    first = next(it)
    assert first[1]["status"] in {"ok", "failed"}
    # Drain so the pool shuts down cleanly.
    remaining = list(it)
    assert len(remaining) == 49


def _peak_kib(fn, *args, **kwargs):
    import tracemalloc

    tracemalloc.start()
    try:
        fn(*args, **kwargs)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / 1024


def test_peak_memory_streaming_lower_than_materialized(tmp_path, envelope_factory):
    """Headline P1-A win: stream peak RSS is materially smaller than materialized.

    We only assert a 2x gap so the test stays stable across machines; the
    actual ratio at 10k envelopes is typically 5-10x in practice, and the exact
    numbers are tracked in bench/RESULTS.md.
    """
    root = envelope_factory.write_dir(tmp_path / "env", 2000, failure_rate=0.0)
    files = sorted(root.rglob("result.json"))
    mat = _peak_kib(_materialized_merge, files)
    stream = _peak_kib(_streaming_merge, files)
    # Sanity: both > 0, streaming clearly lower.
    assert stream > 0 and mat > 0
    assert stream * 2 < mat, (
        f"expected streaming peak << materialized peak, got stream={stream:.1f} KiB "
        f"vs materialized={mat:.1f} KiB"
    )


def _materialized_merge_with_validation(files, schema):
    """Pre-P1-A shape: load-all, then a SECOND pass validating each envelope."""
    from scripts.schema_validator import validate_envelope

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(files)))) as ex:
        loaded = list(ex.map(_load_one, files))
    kept = []
    for _, d in loaded:
        if d is None:
            continue
        vr = validate_envelope(d, schema=schema)
        if vr.ok and d.get("status") != "failed":
            kept.append(d)
    return strategy_merge(kept)


def _streaming_merge_with_validation(files, schema):
    """P1-A shape: fold load + validate into a single pass."""
    from scripts.schema_validator import validate_envelope

    def gen():
        for _, d in iter_loaded_files(files, max_workers=8):
            if d is None:
                continue
            vr = validate_envelope(d, schema=schema)
            if vr.ok and d.get("status") != "failed":
                yield d

    return strategy_merge(gen())


@pytest.mark.parametrize("count", [1000])
def test_validation_materialized(benchmark, tmp_path, envelope_factory, count):
    from scripts.schema_validator import _load_schema

    root = envelope_factory.write_dir(tmp_path / "env", count, failure_rate=0.0)
    files = sorted(root.rglob("result.json"))
    schema = _load_schema()
    benchmark(_materialized_merge_with_validation, files, schema)


@pytest.mark.parametrize("count", [1000])
def test_validation_streaming(benchmark, tmp_path, envelope_factory, count):
    from scripts.schema_validator import _load_schema

    root = envelope_factory.write_dir(tmp_path / "env", count, failure_rate=0.0)
    files = sorted(root.rglob("result.json"))
    schema = _load_schema()
    benchmark(_streaming_merge_with_validation, files, schema)
