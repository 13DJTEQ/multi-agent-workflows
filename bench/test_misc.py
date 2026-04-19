"""Misc hot-path benchmarks: _parse_run_id, schema validation."""
from __future__ import annotations

import pytest

from scripts.spawn_oz import _parse_run_id
from scripts.schema_validator import validate_envelope, _load_schema


JSON_OUT = '{"run_id": "5972cca4-a410-42af-930a-e56bc23e07ac", "status": "pending"}'
TEXT_OUT = "Spawned agent with run ID: 5972cca4-a410-42af-930a-e56bc23e07ac\nDone."


@pytest.mark.parametrize("inp", [JSON_OUT, TEXT_OUT])
def test_parse_run_id(benchmark, inp):
    benchmark(_parse_run_id, inp)


@pytest.mark.parametrize("count", [100, 1000])
def test_validate_envelope_builtin(benchmark, envelope_factory, count):
    """Baseline envelope validation throughput (sans jsonschema dep)."""
    data = envelope_factory.make(count)
    schema = _load_schema()

    def _run():
        for env in data:
            validate_envelope(env, schema=schema)

    benchmark(_run)
