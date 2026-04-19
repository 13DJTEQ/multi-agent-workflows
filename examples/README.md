# Examples

Runnable end-to-end workflows demonstrating the skill's primitives. Each script expects `docker` and `WARP_API_KEY` (or a keychain-resolved secret via `scripts/credential_helper.py`).

## Scripts

| Script | Demonstrates |
|--------|--------------|
| `parallel-code-review.sh` | Fan-out across subdirectories + merge aggregation |
| `phased-refactor.sh` | DAG-driven multi-phase execution via `dependency_graph.py` |
| `test-sharding.sh` | Round-robin test file sharding across N parallel agents |
| `multi-backend.sh` | Docker-first with Kubernetes fallback |

## Manifests

`manifests/refactor-example.yaml` — Diamond-pattern DAG (scan → parallel deep-dives → synthesis). Feed it to `dependency_graph.py plan` or `phased-refactor.sh`.

## Quick sanity check (no agents spawned)

```bash
# Validate the example manifest
python3 scripts/dependency_graph.py validate examples/manifests/refactor-example.yaml

# Preview the plan
python3 scripts/dependency_graph.py plan examples/manifests/refactor-example.yaml

# See the shell commands it would run
python3 scripts/dependency_graph.py plan examples/manifests/refactor-example.yaml --format phase-commands
```

## Secret setup (one-time)

```bash
# Store API key in macOS Keychain
python3 scripts/credential_helper.py set WARP_API_KEY

# Confirm retrieval
python3 scripts/credential_helper.py get WARP_API_KEY
```
