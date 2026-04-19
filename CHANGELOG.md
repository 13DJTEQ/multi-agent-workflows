# Changelog

All notable changes to the **multi-agent-workflows** skill are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-04-19
Phase 6: cloud-native orchestration + production hardening.
### Added
- **`spawn_oz.py`** (P0-A) — cloud-agent backend using `oz agent run-cloud`; required `--environment`; fire-and-forget by default with opt-in `--wait` polling; captures PR/branch artifacts from agent output; new `maw-spawn-oz` entry point.
- **`scripts/_common.py`** — shared helpers (`calculate_backoff`, `check_circuit_breaker`, `validate_tasks_file`) extracted from spawn_docker.
- **Result-schema enforcement** (P0-B) — `references/result-schema.json`, `scripts/schema_validator.py` (library + CLI; graceful fallback without `jsonschema`), `aggregate_results.py --validate-schema` (opt-in; drops `status='failed'` from merge/concat, aborts on malformed envelopes, adds per-status stats breakdown).
- **Structured logging** (P1-B) — `scripts/_log.py` with `log_event` NDJSON emitter; new `--log-format {text,json}` on spawn_docker/spawn_oz/spawn_k8s/wait_for_phase/aggregate_results; event taxonomy: `spawn.start`, `spawn.circuit_breaker.tripped`, `spawn.container.started`, `spawn.container.completed`, `phase.wait.start`, `phase.wait.done`, `aggregate.start`, `aggregate.done`.
- **Oz cost/metrics surface** (P1-C) — `spawn_oz.py` extracts `tokens_used`/`cost_usd`/`model` from `oz run get`; `aggregate_results.py --include-stats` now emits `stats.metrics_rollup` with totals + per-model breakdown.
### Changed
- **Installer** (P1-A, fixes #4) — `install.sh` prefers `pipx` unconditionally; venv fallback pinned to `python3.12 → python3.11`; refuses Python 3.14 with actionable guidance; `--verify` now executes `maw-spawn-docker --help` instead of just `command -v`.
- **CI** — `unit-tests` job matrices over Python 3.11 and 3.12.
### Fixed
- #4 — `maw-*` CLI wrappers not generated under Python 3.14 venv.
### Tests
- **243/243 passing** on release (up from 169 at v1.0.0).
## [1.0.0] — 2026-04-19

First full release. All five implementation phases merged to `main`.

### Added
- **Core framework** (Phase 1): Docker backend, task decomposition, result aggregation
- **Backend expansion** (Phase 2): Kubernetes, CI (GitHub Actions matrix), Remote/SSH
- **Advanced patterns** (Phase 3): fan-out/fan-in, diamond DAG, sharding strategies, inter-agent communication (shared volumes, checkpoints, Redis, leader election), dynamic scaling
- **Polish + open questions** (Phase 4):
  - Preflight checks (docker daemon, disk space, output writable) with `--skip-preflight` escape hatch
  - `credential_helper.py` with pluggable backends: `env`, `keychain` (macOS), `1password` (read), `vault`/`aws` (scaffold)
  - `dependency_graph.py` with Kahn topological sort, YAML/JSON manifests, cycle detection, DOT visualization
  - `references/context-propagation.md`, `references/result-schema.md`
  - `examples/` directory with 4 runnable workflows + DAG manifest
  - `install.sh` installer + `.github/workflows/test.yml` CI pipeline
- **Oz cloud-agent support** (Phase 5):
  - `OzSecretBackend` wrapping `oz secret create/update/delete/list`
  - Write-only semantics by design (values inject into cloud agents as env vars at runtime)
  - `set()` auto-upgrades to `oz secret update` on "already exists" conflicts
  - `spawn_docker.py --credential-backend oz`
  - New "Running on Oz (cloud agents)" section in SKILL.md with backend + credential guidance

### Tests
- **169/169 passing** on release
- Coverage across all 6 credential backends, all 4 aggregation strategies, DAG planning, K8s spawn, Docker spawn, phase waiting, and eval harness

### Known Issues
- [#4](https://github.com/13DJTEQ/multi-agent-workflows/issues/4) — `install.sh --with-cli` does not generate `maw-*` wrappers under Python 3.14 venv. Low impact; agents use `python3 scripts/...` directly. Tracked for future installer work.
