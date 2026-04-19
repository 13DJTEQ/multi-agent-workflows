#!/usr/bin/env bash
# Example: Multi-phase refactor driven by a DAG manifest.
#
# Uses dependency_graph.py to compute phases from a YAML manifest, then
# spawns each phase sequentially with synchronization between phases.
#
# Usage:
#   ./examples/phased-refactor.sh [MANIFEST]
#
# Default manifest: examples/manifests/refactor-example.yaml

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
MANIFEST="${1:-$SCRIPT_DIR/manifests/refactor-example.yaml}"
OUTPUT_DIR="${OUTPUT_DIR:-./outputs/phased-refactor}"

if [[ ! -f "$MANIFEST" ]]; then
  echo "Error: manifest not found: $MANIFEST" >&2
  exit 1
fi

# Validate & compute plan
echo "Validating manifest..."
python3 "$SKILL_DIR/scripts/dependency_graph.py" validate "$MANIFEST"

echo
echo "Computed plan:"
python3 "$SKILL_DIR/scripts/dependency_graph.py" plan "$MANIFEST"

# Export phase plan as JSON for programmatic spawning
PLAN_JSON="$(mktemp)"
trap 'rm -f "$PLAN_JSON"' EXIT
python3 "$SKILL_DIR/scripts/dependency_graph.py" plan "$MANIFEST" --format json > "$PLAN_JSON"

NUM_PHASES=$(jq -r '.num_phases' "$PLAN_JSON")
echo
echo "Executing $NUM_PHASES phases sequentially..."

for i in $(seq 1 "$NUM_PHASES"); do
  echo
  echo "=== Phase $i ==="
  mapfile -t PROMPTS < <(jq -r ".phases[$((i-1))].tasks[].prompt" "$PLAN_JSON")

  python3 "$SKILL_DIR/scripts/spawn_docker.py" \
    --tasks "${PROMPTS[@]}" \
    --phase "$i" \
    --output-dir "$OUTPUT_DIR/phase-$i" \
    --wait

  if [[ $i -lt $NUM_PHASES ]]; then
    python3 "$SKILL_DIR/scripts/wait_for_phase.py" \
      --phase "$i" \
      --backend docker \
      --min-success 1.0
  fi
done

echo
echo "Aggregating final results..."
python3 "$SKILL_DIR/scripts/aggregate_results.py" \
  --input-dir "$OUTPUT_DIR" \
  --output "$OUTPUT_DIR/final-report.md" \
  --strategy merge \
  --include-provenance

echo "Done. Report: $OUTPUT_DIR/final-report.md"
