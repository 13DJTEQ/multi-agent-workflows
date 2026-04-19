"""Benchmark suite for multi-agent-workflows.

Run with:
    pytest bench/ --benchmark-only
    pytest bench/ --benchmark-only --benchmark-json=bench/baseline.json
    pytest bench/ --benchmark-only --benchmark-compare=bench/baseline.json

Advisory, not a merge gate. Numbers inform perf work but don't block PRs.
"""
