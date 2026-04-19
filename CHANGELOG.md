# Changelog

All notable changes to the **multi-agent-workflows** skill are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
