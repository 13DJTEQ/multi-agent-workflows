#!/usr/bin/env bash
# Example: Shard pytest execution across N parallel agents.
#
# Splits test files into round-robin shards and runs each in its own container.
# Final step concatenates JUnit XML output.
#
# Usage:
#   ./examples/test-sharding.sh [NUM_SHARDS] [TEST_DIR]
#
# Defaults: NUM_SHARDS=4, TEST_DIR=tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
NUM_SHARDS="${1:-4}"
TEST_DIR="${2:-tests}"
OUTPUT_DIR="${OUTPUT_DIR:-./outputs/test-shards}"

if [[ ! -d "$TEST_DIR" ]]; then
  echo "Error: $TEST_DIR does not exist" >&2
  exit 1
fi

# Collect test files
mapfile -t TEST_FILES < <(find "$TEST_DIR" -type f -name "test_*.py" | sort)
TOTAL=${#TEST_FILES[@]}

if [[ $TOTAL -eq 0 ]]; then
  echo "No test files found under $TEST_DIR" >&2
  exit 1
fi

echo "Sharding $TOTAL tests across $NUM_SHARDS agents"

# Build one task prompt per shard (round-robin assignment)
TASKS=()
for i in $(seq 0 $((NUM_SHARDS-1))); do
  SHARD_FILES=""
  for j in "${!TEST_FILES[@]}"; do
    if [[ $((j % NUM_SHARDS)) -eq $i ]]; then
      SHARD_FILES+="${TEST_FILES[$j]} "
    fi
  done
  if [[ -n "$SHARD_FILES" ]]; then
    TASKS+=("pytest --junitxml=/output/junit-$i.xml $SHARD_FILES")
  fi
done

echo "Spawning ${#TASKS[@]} shard agents..."

python3 "$SKILL_DIR/scripts/spawn_docker.py" \
  --tasks "${TASKS[@]}" \
  --workspace "$PWD" \
  --output-dir "$OUTPUT_DIR" \
  --wait \
  --parallel "$NUM_SHARDS"

echo
echo "Concatenating JUnit reports..."
python3 "$SKILL_DIR/scripts/aggregate_results.py" \
  --input-dir "$OUTPUT_DIR" \
  --output "$OUTPUT_DIR/combined-junit.txt" \
  --strategy concat

echo "Done. Combined report: $OUTPUT_DIR/combined-junit.txt"
