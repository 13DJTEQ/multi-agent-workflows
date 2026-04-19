#!/usr/bin/env bash
# Example: Parallel code review across top-level directories.
#
# Spawns one agent per immediate subdirectory of src/ to perform a focused
# review, then aggregates findings into a single report.
#
# Usage:
#   ./examples/parallel-code-review.sh [SRC_DIR]
#
# Requires: docker, $WARP_API_KEY (or use credential_helper.py).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="${1:-src}"
OUTPUT_DIR="${OUTPUT_DIR:-./outputs/code-review}"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Error: $SRC_DIR does not exist" >&2
  exit 1
fi

# Build task list: one per subdir (portable across macOS/Linux)
mapfile -t TASKS < <(
  find "$SRC_DIR" -mindepth 1 -maxdepth 1 -type d \
    | awk -F/ '{print "Review module " $NF}'
)

if [[ ${#TASKS[@]} -eq 0 ]]; then
  echo "No subdirectories to review in $SRC_DIR" >&2
  exit 1
fi

echo "Spawning ${#TASKS[@]} reviewers for $SRC_DIR ..."

python3 "$SKILL_DIR/scripts/spawn_docker.py" \
  --tasks "${TASKS[@]}" \
  --workspace "$PWD" \
  --output-dir "$OUTPUT_DIR" \
  --wait \
  --retry-failed \
  --max-retries 2 \
  --json > "$OUTPUT_DIR/spawn-summary.json"

echo
echo "Aggregating results..."
python3 "$SKILL_DIR/scripts/aggregate_results.py" \
  --input-dir "$OUTPUT_DIR" \
  --output "$OUTPUT_DIR/final-report.md" \
  --strategy merge \
  --include-stats

echo
echo "Done. Report: $OUTPUT_DIR/final-report.md"
