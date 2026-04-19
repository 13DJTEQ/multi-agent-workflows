#!/usr/bin/env python3
"""
Aggregate results from multiple parallel agents.

Usage:
    python3 aggregate_results.py --input-dir ./outputs --output ./report.json
    python3 aggregate_results.py --input-dir ./outputs --strategy concat --output ./report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

# Structured logging (optional; degrades gracefully if unavailable)
try:
    from scripts._log import configure as _log_configure, log_event, add_log_format_arg  # type: ignore
except ImportError:
    try:
        from ._log import configure as _log_configure, log_event, add_log_format_arg  # type: ignore
    except ImportError:
        def _log_configure(*_a, **_k): ...
        def log_event(*_a, **_k): ...
        def add_log_format_arg(_p): ...

T = TypeVar("T")


def load_json_file(path: Path) -> Optional[dict]:
    """Load a JSON file, returning None on error."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def load_text_file(path: Path) -> Optional[str]:
    """Load a text file, returning None on error."""
    try:
        return path.read_text()
    except Exception:
        return None


def find_result_files(input_dir: Path, pattern: str = "result.json") -> list[Path]:
    """Find all result files in the input directory.
    
    Search order: exact pattern match > any .json > any .md
    """
    # Direct files matching pattern
    results = list(input_dir.glob(f"**/{pattern}"))
    
    # Fallback to any JSON files
    if not results:
        results = list(input_dir.glob("**/*.json"))
    
    # Fallback to markdown files for concat strategy
    if not results:
        results = list(input_dir.glob("**/*.md"))
    
    return sorted(results)


def merge_dicts(dicts: list[dict], policy: str = "last") -> dict:
    """Merge multiple dictionaries.

    Fast paths:
      - policy='last'  : chain of dict.update() (C-loop, ~4x faster than pure-Python
        key iteration — see /tmp/maw-bench 'Pass 3' integration results).
      - policy='first' : iterate in reverse so that earlier dicts overwrite later
        dicts, giving first-seen-wins semantics with a single dict.update chain.
    Slow path preserved for 'concat' and 'error', which require per-key logic.
    """
    if policy == "last":
        result: dict = {}
        for d in dicts:
            result.update(d)
        return result

    if policy == "first":
        result = {}
        for d in reversed(dicts):
            result.update(d)
        return result

    # 'concat' and 'error' policies: require per-key inspection.
    result = {}
    for d in dicts:
        for key, value in d.items():
            if key not in result:
                result[key] = value
            elif policy == "concat" and isinstance(result[key], list) and isinstance(value, list):
                result[key] = result[key] + value
            elif policy == "error":
                raise ValueError(f"Conflict on key: {key}")

    return result


def strategy_merge(
    results: list[dict],
    merge_policy: str = "last",
    **kwargs,
) -> dict:
    """Merge strategy: combine non-conflicting outputs."""
    return merge_dicts(results, policy=merge_policy)


def strategy_concat(
    results: list[Any],
    separator: str = "\n\n",
    **kwargs,
) -> str:
    """Concat strategy: append all outputs sequentially."""
    text_results = []
    for r in results:
        if isinstance(r, str):
            text_results.append(r)
        elif isinstance(r, dict):
            # Try to extract text content
            if "content" in r:
                text_results.append(r["content"])
            elif "text" in r:
                text_results.append(r["text"])
            elif "output" in r:
                text_results.append(r["output"])
            else:
                text_results.append(json.dumps(r, indent=2))
        else:
            text_results.append(str(r))
    
    return separator.join(text_results)


def strategy_vote(
    results: list[dict],
    vote_field: str = "decision",
    vote_threshold: float = 0.5,
    weighted: bool = False,
    confidence_field: str = "confidence",
    **kwargs,
) -> dict:
    """Vote strategy: use majority for boolean/choice outputs."""
    votes = []
    confidences = []
    
    for r in results:
        if vote_field in r:
            votes.append(r[vote_field])
            if weighted and confidence_field in r:
                confidences.append(r[confidence_field])
            else:
                confidences.append(1.0)
    
    if not votes:
        return {"error": f"No votes found for field: {vote_field}"}
    
    # Count votes (weighted if requested)
    if weighted:
        vote_weights: dict[Any, float] = {}
        for vote, conf in zip(votes, confidences):
            key = str(vote)
            vote_weights[key] = vote_weights.get(key, 0) + conf
        
        winner = max(vote_weights.items(), key=lambda x: x[1])
        total_weight = sum(vote_weights.values())
        
        return {
            vote_field: winner[0] == "True" if winner[0] in ("True", "False") else winner[0],
            "vote_weights": vote_weights,
            "total_weight": total_weight,
            "winner_weight": winner[1],
            "winner_ratio": winner[1] / total_weight if total_weight else 0,
        }
    else:
        counter = Counter(str(v) for v in votes)
        total = len(votes)
        winner, count = counter.most_common(1)[0]
        
        return {
            vote_field: winner == "True" if winner in ("True", "False") else winner,
            "vote_count": dict(counter),
            "total_votes": total,
            "winner_count": count,
            "winner_ratio": count / total if total else 0,
            "threshold_met": (count / total) >= vote_threshold if total else False,
        }


def strategy_latest(
    results: list[dict],
    timestamp_field: str = "timestamp",
    **kwargs,
) -> dict:
    """Latest strategy: take most recent output per key."""
    # Sort by timestamp
    def get_timestamp(r: dict) -> datetime:
        ts = r.get(timestamp_field, "1970-01-01T00:00:00")
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return datetime.min
    
    sorted_results = sorted(results, key=get_timestamp, reverse=True)
    
    if sorted_results:
        return sorted_results[0]
    return {}


STRATEGIES: dict[str, Callable] = {
    "merge": strategy_merge,
    "concat": strategy_concat,
    "vote": strategy_vote,
    "latest": strategy_latest,
}


def _rollup_metrics(results: list[Any]) -> dict:
    """Sum metrics.* fields across result envelopes (P1-C).

    Only operates on entries that look like the v1 envelope
    (dict with a ``metrics`` sub-dict). Returns totals plus a per-model
    breakdown when ``metrics.model`` is present.
    """
    total_tokens = 0
    total_cost = 0.0
    total_duration = 0.0
    per_model: dict[str, dict] = {}
    saw_any = False
    for r in results:
        if not isinstance(r, dict):
            continue
        m = r.get("metrics")
        if not isinstance(m, dict):
            continue
        saw_any = True
        tokens = m.get("tokens_used")
        cost = m.get("cost_usd")
        dur = m.get("duration_seconds")
        model = m.get("model")
        if isinstance(tokens, (int, float)):
            total_tokens += int(tokens)
        if isinstance(cost, (int, float)):
            total_cost += float(cost)
        if isinstance(dur, (int, float)):
            total_duration += float(dur)
        if isinstance(model, str):
            bucket = per_model.setdefault(model, {"count": 0, "tokens_used": 0, "cost_usd": 0.0})
            bucket["count"] += 1
            if isinstance(tokens, (int, float)):
                bucket["tokens_used"] += int(tokens)
            if isinstance(cost, (int, float)):
                bucket["cost_usd"] += float(cost)
    if not saw_any:
        return {}
    result: dict = {}
    if total_tokens:
        result["total_tokens"] = total_tokens
    if total_cost:
        result["total_cost_usd"] = round(total_cost, 6)
    if total_duration:
        result["total_duration_seconds"] = round(total_duration, 3)
    if per_model:
        result["per_model"] = per_model
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate results from parallel agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Strategies:
  merge   - Combine dict outputs (last-wins on conflicts)
  concat  - Append all outputs sequentially
  vote    - Majority vote for boolean/choice fields
  latest  - Take most recent output by timestamp

Examples:
  %(prog)s --input-dir ./outputs -o report.json --strategy merge
  %(prog)s --input-dir ./outputs -o report.md --strategy concat
  %(prog)s --input-files a.json b.json c.json -o vote.json --strategy vote --vote-field approved
""",
    )
    
    # Input options
    input_group = parser.add_argument_group("Input (one required)")
    input_group.add_argument("--input-dir", type=Path, metavar="DIR", help="Directory containing agent outputs")
    input_group.add_argument("--input-files", nargs="+", type=Path, metavar="FILE", help="Specific files to aggregate")
    input_group.add_argument("--pattern", default="result.json", help="File pattern to match (default: %(default)s)")
    
    # Output options
    parser.add_argument("--output", "-o", type=Path, required=True, help="Output file path")
    parser.add_argument("--format", choices=["json", "yaml", "markdown", "csv"], help="Output format (auto-detected from extension)")
    
    # Strategy options
    parser.add_argument("--strategy", "-s", default="merge", choices=list(STRATEGIES.keys()), help="Aggregation strategy")
    parser.add_argument("--merge-policy", default="last", choices=["last", "first", "concat", "error"], help="Merge conflict policy")
    parser.add_argument("--concat-separator", default="\n\n", help="Separator for concat strategy")
    parser.add_argument("--vote-field", default="decision", help="Field to vote on")
    parser.add_argument("--vote-threshold", type=float, default=0.5, help="Vote threshold for majority")
    parser.add_argument("--vote-weighted", action="store_true", help="Weight votes by confidence")
    parser.add_argument("--timestamp-field", default="timestamp", help="Timestamp field for latest strategy")
    
    # Error handling
    parser.add_argument("--allow-partial", action="store_true", help="Allow partial results (some failures)")
    parser.add_argument("--min-success", type=float, default=0.0, help="Minimum success ratio required")
    parser.add_argument("--strict", action="store_true", help="Fail if any agent failed")
    parser.add_argument("--skip-invalid", action="store_true", help="Skip invalid/unparseable files")
    
    # Metadata
    parser.add_argument("--include-provenance", action="store_true", help="Include source info")
    parser.add_argument("--include-stats", action="store_true", help="Include aggregation statistics")

    # Schema enforcement (opt-in; see references/result-schema.md)
    parser.add_argument(
        "--validate-schema",
        action="store_true",
        help="Validate each input against references/result-schema.json (v1 envelope). "
             "Drops status=='failed' entries from merge/concat; malformed envelopes abort.",
    )
    add_log_format_arg(parser)

    args = parser.parse_args()
    _log_configure(format=getattr(args, "log_format", "text"))
    log_event("aggregate.start", strategy=args.strategy, validate_schema=args.validate_schema)
    
    # Collect input files
    input_files = []
    if args.input_files:
        input_files = args.input_files
    elif args.input_dir:
        input_files = find_result_files(args.input_dir, args.pattern)
    else:
        print("Error: Must provide --input-dir or --input-files", file=sys.stderr)
        sys.exit(1)
    
    if not input_files:
        print("Error: No input files found", file=sys.stderr)
        sys.exit(1)
    
    print(f"Found {len(input_files)} result files", file=sys.stderr)
    
    # Load results (parallel for large file counts)
    results: list[Any] = []
    provenance: dict[str, dict] = {}
    failed_files: list[str] = []
    
    def load_file(f: Path) -> tuple[Path, Any]:
        """Load a single file, return (path, data or None)."""
        if f.suffix == ".json":
            return f, load_json_file(f)
        return f, load_text_file(f)
    
    # Use threads for I/O-bound file loading when many files
    if len(input_files) > 10:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(input_files))) as executor:
            loaded = list(executor.map(load_file, input_files))
    else:
        loaded = [load_file(f) for f in input_files]
    
    status_counts: Counter = Counter()
    schema_errors: list[tuple[str, list[str]]] = []
    validator_schema = None
    if args.validate_schema:
        try:
            from scripts.schema_validator import validate_envelope, _load_schema  # type: ignore
        except ImportError:
            try:
                from .schema_validator import validate_envelope, _load_schema  # type: ignore
            except ImportError:
                sys.path.insert(0, str(Path(__file__).parent))
                from schema_validator import validate_envelope, _load_schema  # type: ignore
        validator_schema = _load_schema()

    for f, data in loaded:
        if data is None:
            failed_files.append(str(f))
            if not args.skip_invalid:
                print(f"Warning: Failed to load {f}", file=sys.stderr)
            continue

        if args.validate_schema and isinstance(data, dict):
            vr = validate_envelope(data, schema=validator_schema)
            if not vr.ok:
                schema_errors.append((str(f), vr.errors))
                failed_files.append(str(f))
                print(f"Schema: {f} ✗ {'; '.join(vr.errors)}", file=sys.stderr)
                continue
            status = data.get("status")
            status_counts[str(status)] += 1
            # Drop status=='failed' entries from aggregation per migration plan
            if status == "failed":
                continue

        results.append(data)
        if args.include_provenance:
            agent_id = f.parent.name if f.parent != args.input_dir else f.stem
            provenance[agent_id] = {
                "file": str(f),
                "timestamp": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }

    if args.validate_schema and schema_errors:
        print(
            f"Error: {len(schema_errors)} envelope(s) failed schema validation. Aborting.",
            file=sys.stderr,
        )
        sys.exit(1)
    
    # Check success ratio
    total = len(input_files)
    successful = len(results)
    success_ratio = successful / total if total else 0
    
    if args.strict and failed_files:
        print(f"Error: {len(failed_files)} files failed to load (strict mode)", file=sys.stderr)
        sys.exit(1)
    
    if success_ratio < args.min_success:
        print(f"Error: Success ratio {success_ratio:.1%} below minimum {args.min_success:.1%}", file=sys.stderr)
        sys.exit(1)
    
    if not results:
        print("Error: No valid results to aggregate", file=sys.stderr)
        sys.exit(1)
    
    # Run aggregation
    start_time = datetime.now()
    
    strategy_fn = STRATEGIES[args.strategy]
    aggregated = strategy_fn(
        results,
        merge_policy=args.merge_policy,
        separator=args.concat_separator,
        vote_field=args.vote_field,
        vote_threshold=args.vote_threshold,
        weighted=args.vote_weighted,
        timestamp_field=args.timestamp_field,
    )
    
    end_time = datetime.now()
    
    # Build final output
    if args.include_provenance or args.include_stats:
        if isinstance(aggregated, dict):
            output = {"data": aggregated}
        else:
            output = {"data": str(aggregated)}
        
        if args.include_provenance:
            output["provenance"] = provenance
        
        if args.include_stats:
            output["stats"] = {
                "total_files": total,
                "successful": successful,
                "failed": len(failed_files),
                "success_ratio": success_ratio,
                "strategy": args.strategy,
                "aggregation_time_ms": (end_time - start_time).total_seconds() * 1000,
                "timestamp": end_time.isoformat(),
            }
            if failed_files:
                output["stats"]["failed_files"] = failed_files
            if args.validate_schema and status_counts:
                output["stats"]["status_breakdown"] = dict(status_counts)
            # Cost/perf rollup (P1-C): sum metrics.* across envelope-shaped inputs.
            cost_rollup = _rollup_metrics(results)
            if cost_rollup:
                output["stats"]["metrics_rollup"] = cost_rollup
    else:
        output = aggregated
    
    # Determine output format
    output_format = args.format
    if not output_format:
        suffix = args.output.suffix.lower()
        format_map = {
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".csv": "csv",
        }
        output_format = format_map.get(suffix, "json")
    
    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    
    if output_format == "json":
        args.output.write_text(json.dumps(output, indent=2, default=str))
    elif output_format == "yaml":
        import yaml
        args.output.write_text(yaml.dump(output, default_flow_style=False))
    elif output_format == "markdown":
        if isinstance(output, str):
            args.output.write_text(output)
        elif isinstance(output, dict) and "data" in output:
            args.output.write_text(str(output["data"]))
        else:
            args.output.write_text(json.dumps(output, indent=2, default=str))
    elif output_format == "csv":
        import csv
        if isinstance(output, list):
            with open(args.output, "w", newline="") as f:
                if output and isinstance(output[0], dict):
                    writer = csv.DictWriter(f, fieldnames=output[0].keys())
                    writer.writeheader()
                    writer.writerows(output)
        else:
            print("Warning: CSV format requires list output, writing as JSON", file=sys.stderr)
            args.output.write_text(json.dumps(output, indent=2, default=str))
    
    print(f"✓ Aggregated {successful} results to {args.output}", file=sys.stderr)
    log_event(
        "aggregate.done",
        strategy=args.strategy,
        total=total,
        successful=successful,
        failed=len(failed_files),
        success_ratio=success_ratio,
        output=str(args.output),
    )

    if args.include_stats:
        print(f"  Strategy: {args.strategy}", file=sys.stderr)
        print(f"  Success rate: {success_ratio:.1%}", file=sys.stderr)


if __name__ == "__main__":
    main()
