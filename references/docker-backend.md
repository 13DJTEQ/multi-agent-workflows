# Docker Backend Reference

Detailed patterns for running multi-agent workflows in Docker containers.

## Prerequisites

- Docker installed and running (`docker info` should succeed)
- `WARP_API_KEY` environment variable set
- Network access to pull images from Docker Hub

## Image Selection

| Image | Use Case | Size |
|-------|----------|------|
| `warpdotdev/dev-base:latest` | General purpose | ~500MB |
| `warpdotdev/dev-base:latest-agents` | Includes third-party CLIs | ~1.2GB |
| `warpdotdev/dev-web:latest` | Node.js/frontend projects | ~800MB |
| `warpdotdev/dev-rust:1.85` | Rust projects | ~1.5GB |
| `warpdotdev/dev-go:1.23` | Go projects | ~700MB |

## Spawning Patterns

### Basic Spawn

```bash
docker run -d \
  --name "agent-$TASK_ID" \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e WARP_API_KEY="$WARP_API_KEY" \
  warpdotdev/dev-base:latest \
  oz agent run --prompt "$PROMPT" --share team
```

### With Output Directory

```bash
mkdir -p outputs/$TASK_ID

docker run -d \
  --name "agent-$TASK_ID" \
  -v "$PWD:/workspace" \
  -v "$PWD/outputs/$TASK_ID:/output" \
  -w /workspace \
  -e WARP_API_KEY="$WARP_API_KEY" \
  -e OUTPUT_DIR="/output" \
  warpdotdev/dev-base:latest \
  oz agent run --prompt "$PROMPT. Save results to /output/result.json" --share team
```

### With Resource Limits

```bash
docker run -d \
  --name "agent-$TASK_ID" \
  --memory="4g" \
  --cpus="2" \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e WARP_API_KEY="$WARP_API_KEY" \
  warpdotdev/dev-base:latest \
  oz agent run --prompt "$PROMPT" --share team
```

### With Network Isolation

```bash
# Create isolated network
docker network create agent-net

# Spawn agents on network
docker run -d \
  --name "agent-$TASK_ID" \
  --network agent-net \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e WARP_API_KEY="$WARP_API_KEY" \
  warpdotdev/dev-base:latest \
  oz agent run --prompt "$PROMPT" --share team
```

## Monitoring

### Container Status

```bash
# List running agents
docker ps --filter "name=agent-" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"

# Watch status
watch -n 5 'docker ps --filter "name=agent-"'
```

### Logs

```bash
# Tail logs for specific agent
docker logs -f agent-task-1

# Get all logs
for c in $(docker ps -a --filter "name=agent-" -q); do
  echo "=== $c ===" >> all-logs.txt
  docker logs $c >> all-logs.txt 2>&1
done
```

### Exit Codes

```bash
# Check exit codes
docker ps -a --filter "name=agent-" --format "{{.Names}}: {{.Status}}"

# Get specific exit code
docker inspect agent-task-1 --format='{{.State.ExitCode}}'
```

## Result Collection

### From Mounted Volumes

```bash
# Results saved to mounted output directory
ls -la outputs/*/result.json

# Combine all results
jq -s '.' outputs/*/result.json > combined.json
```

### From Container Filesystem

```bash
# Copy results from container
docker cp agent-task-1:/workspace/output.json ./outputs/task-1.json

# Batch copy
for c in $(docker ps -a --filter "name=agent-" --format "{{.Names}}"); do
  docker cp $c:/workspace/output.json ./outputs/$c.json 2>/dev/null || true
done
```

### From Logs

```bash
# Extract structured output from logs
docker logs agent-task-1 2>&1 | grep "^{" | jq '.'
```

## Cleanup

### Remove Completed Containers

```bash
# Remove all agent containers
docker rm $(docker ps -a --filter "name=agent-" -q)

# Remove only exited containers
docker rm $(docker ps -a --filter "name=agent-" --filter "status=exited" -q)
```

### Cleanup Script

```bash
#!/bin/bash
# cleanup_agents.sh

echo "Stopping running agents..."
docker stop $(docker ps --filter "name=agent-" -q) 2>/dev/null

echo "Removing agent containers..."
docker rm $(docker ps -a --filter "name=agent-" -q) 2>/dev/null

echo "Removing agent network..."
docker network rm agent-net 2>/dev/null

echo "Cleanup complete."
```

## Troubleshooting

### Container Won't Start

```bash
# Check for name conflicts
docker ps -a --filter "name=agent-task-1"

# Remove existing container
docker rm -f agent-task-1

# Check image exists
docker images | grep warpdotdev
```

### Permission Issues

```bash
# Run with user mapping
docker run -d \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/workspace" \
  ...
```

### Network Issues

```bash
# Check container can reach Warp
docker exec agent-task-1 curl -s https://app.warp.dev/health

# Check DNS resolution
docker exec agent-task-1 nslookup app.warp.dev
```

## Advanced: Docker Compose

For complex multi-agent setups, use Docker Compose:

```yaml
# docker-compose.agents.yml
version: '3.8'

services:
  agent-analysis:
    image: warpdotdev/dev-base:latest
    volumes:
      - .:/workspace
      - ./outputs/analysis:/output
    working_dir: /workspace
    environment:
      - WARP_API_KEY=${WARP_API_KEY}
    command: oz agent run --prompt "Analyze codebase architecture" --share team

  agent-tests:
    image: warpdotdev/dev-base:latest
    volumes:
      - .:/workspace
      - ./outputs/tests:/output
    working_dir: /workspace
    environment:
      - WARP_API_KEY=${WARP_API_KEY}
    command: oz agent run --prompt "Review test coverage" --share team

  agent-docs:
    image: warpdotdev/dev-base:latest
    volumes:
      - .:/workspace
      - ./outputs/docs:/output
    working_dir: /workspace
    environment:
      - WARP_API_KEY=${WARP_API_KEY}
    command: oz agent run --prompt "Update documentation" --share team
```

Run with:
```bash
docker-compose -f docker-compose.agents.yml up -d
docker-compose -f docker-compose.agents.yml logs -f
```
