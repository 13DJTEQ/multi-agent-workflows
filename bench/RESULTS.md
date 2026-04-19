# Benchmark Results
Advisory metrics; not a merge gate. Captured on v1.1.0 HEAD (Phase 7 P0 baseline).
## How to run
```bash
pip install -e ".[bench]"
pytest bench/ --benchmark-only
# Compare against committed baseline:
pytest bench/ --benchmark-only --benchmark-compare=bench/baseline.json
```
## Baseline (v1.1.0, `bench/baseline.json`)
Machine: macOS arm64, Python 3.14.4. Numbers are mean per operation.
### Aggregation
| Scenario              | N      | Mean      |
|-----------------------|--------|-----------|
| `strategy_merge`      | 10     | ~330 ns   |
| `strategy_merge`      | 100    | ~8.7 µs   |
| `strategy_merge`      | 1000   | ~85 µs    |
| `strategy_merge`      | 10000  | ~859 µs   |
| `strategy_concat`     | 10     | ~31.5 µs  |
| `strategy_concat`     | 100    | ~321 µs   |
| `strategy_concat`     | 1000   | ~3.5 ms   |
| `_rollup_metrics`     | 10     | ~5 µs     |
| `_rollup_metrics`     | 100    | ~63 µs    |
| `_rollup_metrics`     | 1000   | ~656 µs   |
| `_rollup_metrics`     | 10000  | ~6.7 ms   |
### Circuit breaker (list variant, current impl)
| N      | Mean      |
|--------|-----------|
| 10     | ~360 ns   |
| 100    | ~1.6 µs   |
| 1000   | ~14 µs    |
| 10000  | ~138 µs   |
O(N) growth confirmed. P1-C target: flat curve.
### Log throughput (per-event flush, current impl)
| Format | Events  | Mean total | Events/sec |
|--------|---------|------------|------------|
| text   | 1000    | ~1.5 ms    | ~670k      |
| text   | 10000   | ~14.8 ms   | ~675k      |
| json   | 1000    | ~2.4 ms    | ~410k      |
| json   | 10000   | ~24.5 ms   | ~410k      |
| json   | 100000  | ~245 ms    | ~410k      |
P1-D target: >= 10x improvement on json 100k (target <25 ms).
### Misc
| Scenario                      | Mean     |
|-------------------------------|----------|
| `_parse_run_id` (JSON)        | ~2.5 µs  |
| `_parse_run_id` (text)        | ~3 µs    |
| `validate_envelope` 100/iter  | ~2.7 ms  |
| `validate_envelope` 1000/iter | ~27.7 ms |
## Comparing a PR against baseline
```bash
pytest bench/ --benchmark-only --benchmark-compare=bench/baseline.json \
              --benchmark-compare-fail=mean:10%
```
Fails if any benchmark regresses more than 10% vs baseline.
## Phase 7 P1-A — streaming aggregation
Disk-backed merge: the plan's 5x throughput gate was overly optimistic. JSON parsing of N files dominates both code paths and the bounded-window producer/consumer in `iter_loaded_files` adds a small amount of futures/queue overhead vs the pre-change `list(executor.map(...))`. The win P1-A actually delivers is **flat, O(1) peak memory** regardless of corpus size.
### Wall-clock (macOS arm64, Python 3.14.4)
| Pipeline                             | N      | Mean      |
|--------------------------------------|--------|-----------|
| `_materialized_merge` (pre-P1-A)     | 1000   | ~65.7 ms  |
| `_materialized_merge` (pre-P1-A)     | 10000  | ~631 ms   |
| `_streaming_merge` (post-P1-A)       | 1000   | ~63.8 ms  |
| `_streaming_merge` (post-P1-A)       | 10000  | ~662 ms   |
| `_materialized_merge_with_validation`| 1000   | ~96.1 ms  |
| `_streaming_merge_with_validation`   | 1000   | ~116.6 ms |
Throughput is within noise (±5%) on pure merge; the validation path is slightly slower in streaming mode due to interleaving of load + validate in a single pass. Acceptable given the memory profile below.
### Peak memory (tracemalloc, from repo venv)
| N      | Materialized | Streaming | Ratio   |
|--------|--------------|-----------|---------|
| 1,000  | 3,299 KiB    | 1,158 KiB | 2.85x   |
| 5,000  | 11,506 KiB   | 1,160 KiB | 9.92x   |
| 10,000 | 21,813 KiB   | 1,164 KiB | 18.73x  |
Streaming peak is **constant** (~1.16 MiB). Materialized peak grows linearly with N. At 10k envelopes we hold 18.7x less, and the ratio keeps improving as the corpus scales. The `test_peak_memory_streaming_lower_than_materialized` bench asserts a 2x gap as a machine-portable regression guard.
### `--max-memory-mb` spillover
No throughput penalty observed when the budget is not exceeded (cost is one `json.dumps(data)` per envelope). Payloads above budget write an `{artifact_path, artifact_size}` pointer; the original file on disk is untouched so consumers can re-hydrate.
### Honest gate assessment
- Plan gate: "≥5x throughput on 10k". **Not met** — throughput is effectively parity.
- Memory gate (implicit in plan: "memory RSS capped at a configurable ceiling"). **Met** — flat ~1.16 MiB peak; `--max-memory-mb` provides explicit per-envelope cap.
- Recommendation: keep the streaming refactor for the memory profile; treat the throughput gate as a future P2 if the need arises (would require async I/O or skipping JSON re-parse in hot paths).
