#!/usr/bin/env bash
# Example: Multi-backend workflow with Docker-first, Kubernetes-fallback.
#
# Tries Docker locally. If the Docker daemon is unavailable and kubectl is
# configured, falls back to Kubernetes Jobs.
#
# Usage:
#   ./examples/multi-backend.sh "Task 1" "Task 2" ...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="${OUTPUT_DIR:-./outputs/multi-backend}"

if [[ $# -eq 0 ]]; then
  echo "Usage: $0 \"Task 1\" [\"Task 2\" ...]" >&2
  exit 1
fi

pick_backend() {
  if docker info >/dev/null 2>&1; then
    echo "docker"
    return
  fi
  if command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
    echo "k8s"
    return
  fi
  echo "none"
}

BACKEND="$(pick_backend)"
echo "Selected backend: $BACKEND"

case "$BACKEND" in
  docker)
    python3 "$SKILL_DIR/scripts/spawn_docker.py" \
      --tasks "$@" \
      --workspace "$PWD" \
      --output-dir "$OUTPUT_DIR" \
      --wait
    ;;
  k8s)
    python3 "$SKILL_DIR/scripts/spawn_k8s.py" \
      --tasks "$@" \
      --namespace "${NAMESPACE:-warp-agents}" \
      --wait
    ;;
  none)
    echo "Error: neither Docker nor Kubernetes is available." >&2
    echo "Install Docker Desktop or configure kubectl." >&2
    exit 2
    ;;
esac

python3 "$SKILL_DIR/scripts/aggregate_results.py" \
  --input-dir "$OUTPUT_DIR" \
  --output "$OUTPUT_DIR/final-report.md" \
  --strategy merge

echo "Done. Report: $OUTPUT_DIR/final-report.md"
