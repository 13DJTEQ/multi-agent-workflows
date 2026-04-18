---
name: multi-agent-workflows
description: Orchestrate parallel agent workflows across Docker, Kubernetes, CI, and remote backends. Use when decomposing complex tasks into parallelizable subtasks, running parallel code analysis, fan-out refactoring, or coordinating multiple agents on independent work. Triggers include "parallelize this", "run multiple agents", "fan out", "distribute tasks", "orchestrate agents", or any task that would benefit from concurrent execution across isolated environments.
---

# Multi-Agent Local Workflows

Orchestrate multiple local agents working in parallel across heterogeneous execution backends.

## When to Use This Skill

Use this skill when:
- A task can be decomposed into independent subtasks
- You need isolated execution environments for parallel work
- Results from multiple agents need to be aggregated
- You want local-first execution without cloud dependencies

**Do NOT use when:**
- `/orchestrate` suffices (simple parallel tasks with built-in UI)
- `oz agent run-cloud` is better (managed cloud execution)
- Tasks have strict sequential dependencies

## Quick Start (Docker)

```bash
# 1. Decompose task into subtasks
SUBTASKS=("Analyze auth module" "Analyze database layer" "Analyze API routes")

# 2. Spawn parallel agents
for task in "${SUBTASKS[@]}"; do
  docker run -d \
    -v "$PWD:/workspace" \
    -w /workspace \
    -e WARP_API_KEY="$WARP_API_KEY" \
    --name "agent-$(echo $task | tr ' ' '-' | tr '[:upper:]' '[:lower:]')" \
    warpdotdev/dev-base:latest \
    oz agent run --prompt "$task" --share team
done

# 3. Monitor progress
docker ps --filter "name=agent-"

# 4. Aggregate results
python3 <skill_dir>/scripts/aggregate_results.py /workspace/outputs/
```

## Core Workflow

### Step 1: Task Decomposition

Break the task into parallelizable subtasks. Good candidates:
- **By module/directory**: Each agent handles one part of the codebase
- **By file type**: One agent for tests, another for implementation
- **By operation**: Analysis, refactoring, documentation in parallel
- **By data shard**: Split large datasets across agents

**Decomposition principles:**
1. Subtasks should be independent (no blocking dependencies)
2. Each subtask should be completable in isolation
3. Results should be mergeable without conflicts
4. Failure of one subtask shouldn't block others

### Step 2: Select Backend

Choose based on your environment:

| Backend | Best For | Requirements |
|---------|----------|--------------|
| Docker | Local parallelization with isolation | Docker installed |
| Kubernetes | Existing K8s cluster, scaling needs | kubectl access |
| CI | Audit trail, PR integration | CI system + secrets |
| Remote/SSH | VPN resources, specific hardware | SSH access |

For detailed backend setup, see `references/<backend>-backend.md`.

### Step 3: Spawn Agents

**Docker (default):**
```bash
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks "task1" "task2" "task3" \
  --image warpdotdev/dev-base:latest \
  --workspace /path/to/workspace \
  --output-dir /path/to/outputs
```

**Kubernetes:**
```bash
python3 <skill_dir>/scripts/spawn_k8s.py \
  --tasks "task1" "task2" "task3" \
  --namespace warp-agents \
  --image warpdotdev/dev-base:latest
```

**CI (GitHub Actions):**
Use matrix strategy in workflow — see `references/ci-backend.md`.

**Remote/SSH:**
```bash
for host in host1 host2 host3; do
  ssh $host "cd /workspace && oz agent run --prompt '$task'" &
done
wait
```

### Step 4: Monitor Progress

**Docker:**
```bash
# Watch container status
watch docker ps --filter "name=agent-"

# Tail logs
docker logs -f agent-task-1

# Check all outputs
ls -la /workspace/outputs/
```

**Session sharing (all backends):**
Each agent run with `--share team` can be monitored in the Oz dashboard or via API.

### Step 5: Aggregate Results

```bash
python3 <skill_dir>/scripts/aggregate_results.py \
  --input-dir /workspace/outputs \
  --output /workspace/final-report.md \
  --strategy merge  # or: concat, vote, latest
```

**Aggregation strategies:**
- `merge`: Combine non-conflicting outputs (default)
- `concat`: Append all outputs sequentially
- `vote`: For boolean/choice outputs, use majority
- `latest`: Take most recent output per key

## Error Handling

### Partial Failures

By default, partial failures are tolerated:
```bash
python3 <skill_dir>/scripts/aggregate_results.py \
  --input-dir /workspace/outputs \
  --allow-partial \
  --min-success 0.5  # At least 50% must succeed
```

### Retries

```bash
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks "failed-task" \
  --retry-failed \
  --max-retries 3
```

### Circuit Breaker

Stop spawning if too many failures:
```bash
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks ... \
  --circuit-breaker 0.3  # Stop if >30% fail
```

## Context Propagation

Subagents need context. Options:

1. **Shared volume** (Docker): Mount workspace with context files
2. **Environment variables**: Pass via `-e` flags
3. **Prompt injection**: Include context in the prompt itself
4. **Rules file**: Include `.warp/rules.md` in workspace

```bash
docker run ... \
  -v "$PWD:/workspace" \
  -v "$HOME/.warp:/root/.warp:ro" \
  -e PROJECT_CONTEXT="API refactoring project" \
  ...
```

## Advanced Patterns

### Fan-Out/Fan-In with Dependencies

For multi-phase workflows where later phases depend on earlier ones:

**Basic two-phase pattern:**
```bash
# Phase 1: Analysis (parallel)
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks "analyze-auth" "analyze-api" "analyze-db" \
  --phase 1 \
  --output-dir ./outputs/phase1

# Wait for phase 1 to complete
python3 <skill_dir>/scripts/wait_for_phase.py \
  --phase 1 \
  --backend docker \
  --min-success 1.0

# Phase 2: Synthesis (depends on phase 1)
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks "Synthesize findings from ./outputs/phase1 into unified report" \
  --phase 2
```

**Diamond pattern (A → B,C → D):**
```bash
# Phase 1: Initial analysis
spawn_docker.py --tasks "initial-scan" --phase 1
wait_for_phase.py --phase 1

# Phase 2: Parallel deep dives (both depend on phase 1)
spawn_docker.py --tasks "deep-security" "deep-performance" --phase 2
wait_for_phase.py --phase 2

# Phase 3: Final synthesis (depends on phase 2)
spawn_docker.py --tasks "final-report" --phase 3
```

**Conditional phases:**
```bash
# Run phase 2 only if phase 1 had issues
wait_for_phase.py --phase 1 --output status.json
if jq -e '.issues_found > 0' status.json; then
  spawn_docker.py --tasks "remediation" --phase 2
fi
```

### Sharding Strategies

**By file (round-robin):**
```bash
# Split files evenly across N agents
N=4
find src -name "*.py" | split -n l/$N - /tmp/shard-

# Spawn agent per shard
for i in $(seq 0 $((N-1))); do
  FILES=$(cat /tmp/shard-a$i 2>/dev/null || cat /tmp/shard-$(printf '%c' $((97+i))))
  python3 <skill_dir>/scripts/spawn_docker.py \
    --tasks "Process files: $FILES" \
    --env "SHARD_ID=$i"
done
```

**By module/directory:**
```bash
# One agent per top-level module
for module in $(ls -d src/*/); do
  python3 <skill_dir>/scripts/spawn_docker.py \
    --tasks "Analyze module: $module" \
    --env "MODULE_PATH=$module"
done
```

**By test file (for parallel testing):**
```bash
# Shard test files for faster CI
TEST_FILES=$(find tests -name "test_*.py")
NUM_SHARDS=4

for i in $(seq 0 $((NUM_SHARDS-1))); do
  SHARD=$(echo "$TEST_FILES" | awk "NR % $NUM_SHARDS == $i")
  python3 <skill_dir>/scripts/spawn_docker.py \
    --tasks "pytest $SHARD" \
    --env "SHARD=$i"
done
```

**By data partition:**
```bash
# Split large dataset across agents
TOTAL_RECORDS=1000000
SHARD_SIZE=250000

for offset in $(seq 0 $SHARD_SIZE $((TOTAL_RECORDS-1))); do
  python3 <skill_dir>/scripts/spawn_docker.py \
    --tasks "Process records $offset to $((offset+SHARD_SIZE))" \
    --env "OFFSET=$offset" "LIMIT=$SHARD_SIZE"
done
```

### Inter-Agent Communication

**Shared volume (simplest):**
```bash
# Create shared directory
mkdir -p /tmp/agent-shared

# All agents mount the same volume
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks "task1" "task2" "task3" \
  --env "SHARED_DIR=/shared" \
  --docker-args "-v /tmp/agent-shared:/shared"

# Agents write: /shared/agent-1-result.json
# Agents read: /shared/agent-*-result.json
```

**Checkpoint pattern (for long-running tasks):**
```bash
# Agent writes checkpoints periodically
# checkpoint.json: {"progress": 0.5, "last_item": "item-500"}

# Monitor script can read checkpoints
watch -n 5 'for f in outputs/*/checkpoint.json; do echo "$f:"; cat $f; done'

# On failure, agent can resume from checkpoint
spawn_docker.py \
  --tasks "Resume from checkpoint" \
  --env "CHECKPOINT_FILE=/output/checkpoint.json"
```

**Message queue pattern (for complex coordination):**
```bash
# Using Redis as message broker
docker run -d --name redis -p 6379:6379 redis:alpine

# Agents connect to shared Redis
python3 <skill_dir>/scripts/spawn_docker.py \
  --tasks "producer" "consumer-1" "consumer-2" \
  --docker-args "--link redis:redis" \
  --env "REDIS_URL=redis://redis:6379"

# Producer pushes work items, consumers pull and process
```

**Leader election pattern:**
```bash
# First agent to write leader.lock becomes coordinator
# Other agents poll for leadership or become workers

spawn_docker.py --tasks \
  "Attempt leadership: write /shared/leader.lock if absent, else become worker" \
  "Attempt leadership: write /shared/leader.lock if absent, else become worker" \
  "Attempt leadership: write /shared/leader.lock if absent, else become worker"
```

### Dynamic Scaling

**Scale based on workload:**
```bash
# Count items to process
ITEM_COUNT=$(find data -name "*.json" | wc -l)

# Scale agents (1 per 100 items, min 1, max 10)
AGENT_COUNT=$(( (ITEM_COUNT + 99) / 100 ))
AGENT_COUNT=$(( AGENT_COUNT < 1 ? 1 : AGENT_COUNT ))
AGENT_COUNT=$(( AGENT_COUNT > 10 ? 10 : AGENT_COUNT ))

echo "Spawning $AGENT_COUNT agents for $ITEM_COUNT items"
```

**Auto-retry with backoff:**
```bash
for attempt in 1 2 3; do
  python3 <skill_dir>/scripts/spawn_docker.py --tasks "flaky-task" --wait
  if [ $? -eq 0 ]; then break; fi
  sleep $((attempt * 30))  # 30s, 60s, 90s backoff
done
```

## Backend References

- `references/docker-backend.md` — Docker container patterns
- `references/kubernetes-backend.md` — K8s Jobs, Helm charts
- `references/ci-backend.md` — GitHub Actions, Jenkins
- `references/remote-backend.md` — SSH, rsync patterns
- `references/aggregation-patterns.md` — Result merging strategies

## Comparison with Existing Primitives

| Feature | This Skill | `/orchestrate` | `oz agent run-cloud` |
|---------|------------|----------------|---------------------|
| Backend flexibility | ✅ Docker, K8s, CI, SSH | ❌ Built-in only | ❌ Cloud only |
| Local-first | ✅ | ✅ | ❌ |
| Custom aggregation | ✅ | ❌ | ❌ |
| Managed infra | ❌ | ✅ | ✅ |
| No cloud dependency | ✅ | ✅ | ❌ |

## Troubleshooting

**Agents not starting:**
- Check Docker daemon: `docker info`
- Verify API key: `echo $WARP_API_KEY`
- Check image pull: `docker pull warpdotdev/dev-base:latest`

**Results not aggregating:**
- Check output directory permissions
- Verify agents completed: `docker ps -a --filter "name=agent-"`
- Check individual outputs exist

**Context not propagating:**
- Verify volume mounts: `docker inspect <container>`
- Check environment variables: `docker exec <container> env`
