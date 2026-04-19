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
