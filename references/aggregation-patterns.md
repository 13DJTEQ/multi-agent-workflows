# Aggregation Patterns Reference

Strategies for combining outputs from multiple parallel agents.

## Core Strategies

### Merge (Default)

Combines non-conflicting outputs. Best for structured data where each agent produces different keys.

```python
# Example: Each agent analyzes a different module
agent_1_output = {"auth": {"score": 85, "issues": ["weak password policy"]}}
agent_2_output = {"api": {"score": 92, "issues": []}}
agent_3_output = {"db": {"score": 78, "issues": ["missing indexes"]}}

# Merged result
merged = {
    "auth": {"score": 85, "issues": ["weak password policy"]},
    "api": {"score": 92, "issues": []},
    "db": {"score": 78, "issues": ["missing indexes"]}
}
```

**Usage:**
```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --strategy merge
```

**Conflict handling:**
- Default: Last value wins
- `--merge-policy error`: Fail on conflicts
- `--merge-policy first`: First value wins
- `--merge-policy concat`: Concatenate conflicting arrays

### Concat

Appends all outputs sequentially. Best for text reports or logs.

```python
# Example: Each agent produces a markdown section
agent_1_output = "## Authentication\nPassword policy needs improvement..."
agent_2_output = "## API Routes\nAll endpoints are secure..."
agent_3_output = "## Database\nMissing indexes on users table..."

# Concatenated result
concatenated = """## Authentication
Password policy needs improvement...

## API Routes
All endpoints are secure...

## Database
Missing indexes on users table..."""
```

**Usage:**
```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.md \
  --strategy concat \
  --concat-separator "\n\n---\n\n"
```

### Vote

Uses majority for boolean/choice outputs. Best for classification or approval tasks.

```python
# Example: Each agent votes on whether code is production-ready
agent_1_output = {"ready": True, "confidence": 0.85}
agent_2_output = {"ready": True, "confidence": 0.92}
agent_3_output = {"ready": False, "confidence": 0.78}

# Voted result (majority wins)
voted = {
    "ready": True,  # 2 out of 3 voted True
    "vote_count": {"True": 2, "False": 1},
    "average_confidence": 0.85
}
```

**Usage:**
```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./decision.json \
  --strategy vote \
  --vote-field "ready" \
  --vote-threshold 0.5  # Majority threshold
```

**Options:**
- `--vote-threshold 0.67`: Require supermajority
- `--vote-weighted`: Weight by confidence scores
- `--vote-tie-breaker first`: How to handle ties

### Latest

Takes the most recent output per key. Best for incremental updates.

```python
# Example: Agents updating shared state
agent_1_output = {"status": "analyzing", "progress": 0.3, "timestamp": "2024-01-01T10:00:00"}
agent_2_output = {"status": "complete", "progress": 1.0, "timestamp": "2024-01-01T10:05:00"}

# Latest result
latest = {"status": "complete", "progress": 1.0, "timestamp": "2024-01-01T10:05:00"}
```

**Usage:**
```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./state.json \
  --strategy latest \
  --timestamp-field "timestamp"
```

## Advanced Patterns

### Hierarchical Aggregation

Aggregate in multiple passes for complex workflows:

```bash
# Phase 1: Aggregate analysis results
python3 scripts/aggregate_results.py \
  --input-dir ./outputs/analysis \
  --output ./intermediate/analysis.json \
  --strategy merge

# Phase 2: Aggregate recommendations
python3 scripts/aggregate_results.py \
  --input-dir ./outputs/recommendations \
  --output ./intermediate/recommendations.json \
  --strategy concat

# Phase 3: Final synthesis
python3 scripts/aggregate_results.py \
  --input-files ./intermediate/analysis.json ./intermediate/recommendations.json \
  --output ./final-report.json \
  --strategy merge
```

### Weighted Aggregation

Give different weights to different agents:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --strategy vote \
  --weights '{"senior-agent": 2.0, "junior-agent": 1.0}'
```

### Filtered Aggregation

Only include successful outputs:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --strategy merge \
  --filter-status success \
  --min-success 0.5  # Require at least 50% success
```

### Custom Aggregation Functions

For complex cases, use a custom aggregator:

```python
# custom_aggregator.py
def aggregate(outputs: list[dict]) -> dict:
    """Custom aggregation logic."""
    scores = [o.get("score", 0) for o in outputs]
    issues = []
    for o in outputs:
        issues.extend(o.get("issues", []))
    
    return {
        "average_score": sum(scores) / len(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "all_issues": list(set(issues)),  # Deduplicated
        "issue_count": len(set(issues)),
        "agent_count": len(outputs)
    }
```

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --custom-aggregator ./custom_aggregator.py
```

## Output Formats

### JSON (Default)

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json
```

### Markdown

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.md \
  --format markdown \
  --template ./templates/report.md.j2
```

### CSV

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.csv \
  --format csv \
  --flatten  # Flatten nested structures
```

### YAML

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.yaml \
  --format yaml
```

## Error Handling

### Partial Failures

By default, aggregation tolerates partial failures:

```bash
# Continue if some agents failed
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --allow-partial \
  --min-success 0.5  # At least 50% must succeed
```

### Strict Mode

Fail if any agent failed:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --strict
```

### Missing Data Handling

```bash
# Fill missing fields with defaults
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --fill-missing '{"score": 0, "issues": []}'

# Skip entries with missing required fields
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --require-fields score,issues
```

## Validation

### Schema Validation

Validate outputs against a JSON schema before aggregating:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --schema ./schemas/agent-output.json \
  --skip-invalid  # Skip outputs that don't match schema
```

### Checksum Verification

Verify output integrity:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --verify-checksums  # Check .checksum files if present
```

## Metadata

### Include Provenance

Track which agent produced which output:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --include-provenance
```

Output:
```json
{
  "data": { ... },
  "provenance": {
    "auth": {"agent": "agent-1", "timestamp": "2024-01-01T10:00:00"},
    "api": {"agent": "agent-2", "timestamp": "2024-01-01T10:01:00"}
  }
}
```

### Statistics

Include aggregation statistics:

```bash
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./report.json \
  --include-stats
```

Output:
```json
{
  "data": { ... },
  "stats": {
    "total_agents": 3,
    "successful": 3,
    "failed": 0,
    "aggregation_time_ms": 45,
    "strategy": "merge"
  }
}
```

## Common Patterns

### Code Review Aggregation

```bash
# Each agent reviews different files
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./review.json \
  --strategy merge \
  --group-by file \
  --sort-by severity
```

### Test Result Aggregation

```bash
# Combine test results from parallel runs
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./test-results.json \
  --strategy merge \
  --count-field passed,failed,skipped
```

### Documentation Aggregation

```bash
# Combine docs from multiple agents
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./docs.md \
  --strategy concat \
  --concat-separator "\n\n" \
  --sort-by section_order
```

### Consensus Building

```bash
# Multiple agents evaluate same question
python3 scripts/aggregate_results.py \
  --input-dir ./outputs \
  --output ./consensus.json \
  --strategy vote \
  --vote-field decision \
  --vote-threshold 0.67 \
  --include-dissent  # Show minority opinions
```
