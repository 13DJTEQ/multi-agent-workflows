#!/usr/bin/env python3
"""
Run evaluation cases for multi-agent-workflows skill.

Usage:
    python3 run_evals.py --dry-run           # Validate without spawning
    python3 run_evals.py --case parallel-code-analysis  # Run specific case
    python3 run_evals.py --integration       # Full integration test (requires Docker)
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class EvalResult:
    """Result of evaluating a single test case."""
    case_id: str
    case_name: str
    passed: bool
    score: float  # 0.0 to 1.0
    criteria_results: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_evals(evals_path: Path) -> dict:
    """Load evaluation cases from JSON file."""
    if not evals_path.exists():
        print(f"Error: Evals file not found: {evals_path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(evals_path.read_text())


def analyze_prompt_for_decomposition(prompt: str) -> dict[str, Any]:
    """Analyze a prompt to determine expected decomposition."""
    analysis = {
        "subtask_count": 0,
        "subtask_hints": [],
        "parallel_potential": False,
        "suggested_backend": "docker",
        "sharding_applicable": False,
        "phased_workflow": False,
    }
    
    # Detect directory/module mentions
    dir_pattern = r"(?:directories?|modules?|folders?):\s*([^.]+)"
    dir_match = re.search(dir_pattern, prompt, re.IGNORECASE)
    if dir_match:
        items = re.findall(r"(\w+)/", dir_match.group(1))
        if not items:
            items = re.split(r"[,\s]+and\s+|,\s*", dir_match.group(1).strip())
            items = [i.strip().rstrip("/") for i in items if i.strip()]
        analysis["subtask_count"] = len(items)
        analysis["subtask_hints"] = items
        analysis["parallel_potential"] = len(items) > 1
    
    # Detect sharding keywords
    if any(kw in prompt.lower() for kw in ["split", "shard", "divide", "parallel agents"]):
        analysis["sharding_applicable"] = True
    
    # Detect file count mentions
    file_count_match = re.search(r"(\d+)\s*(?:test\s+)?files?", prompt)
    if file_count_match:
        analysis["file_count"] = int(file_count_match.group(1))
        analysis["sharding_applicable"] = True
    
    # Detect version/environment enumeration (e.g., "Python 3.9, 3.10, 3.11, and 3.12")
    version_matches = re.findall(r"\d+\.\d+", prompt)
    if len(version_matches) >= 2:
        analysis["subtask_count"] = max(analysis["subtask_count"], len(version_matches))
        analysis["parallel_potential"] = True
        analysis["version_enumeration"] = version_matches
    
    # Detect phased workflows (use word boundaries to avoid false matches)
    phase_keywords = [
        r"\bfirst\b", r"\bthen\b", r"\bonce\b", r"\bafter\b",
        r"\bbefore\b", r"\bphase\b", r"\bsynthesis\b",
    ]
    if any(re.search(kw, prompt.lower()) for kw in phase_keywords):
        analysis["phased_workflow"] = True
    
    # Detect backend hints
    if "k8s" in prompt.lower() or "kubernetes" in prompt.lower():
        analysis["suggested_backend"] = "k8s"
    elif "ci" in prompt.lower() or "github" in prompt.lower() or "actions" in prompt.lower():
        analysis["suggested_backend"] = "ci"
    elif "ssh" in prompt.lower() or "remote" in prompt.lower():
        analysis["suggested_backend"] = "remote"
    
    # Detect agent count
    agent_match = re.search(r"(\d+)\s*(?:parallel\s+)?agents?", prompt)
    if agent_match:
        analysis["explicit_agent_count"] = int(agent_match.group(1))
    
    return analysis


def validate_decomposition(analysis: dict, expected: list[str]) -> tuple[bool, list[str]]:
    """Validate that analysis matches expected decomposition criteria."""
    errors = []
    
    for criterion in expected:
        criterion_lower = criterion.lower()
        
        # Check subtask count
        if "decomposes" in criterion_lower or "subtask" in criterion_lower:
            count_match = re.search(r"(\d+)", criterion)
            if count_match:
                expected_count = int(count_match.group(1))
                if analysis.get("subtask_count", 0) < expected_count:
                    if not analysis.get("parallel_potential"):
                        errors.append(f"Expected {expected_count} subtasks, analysis found {analysis.get('subtask_count', 0)}")
        
        # Check backend selection (word-boundary matching)
        if re.search(r"\bkubernetes\b|\bk8s\b", criterion_lower):
            if analysis.get("suggested_backend") != "k8s":
                errors.append(f"Expected K8s backend, got {analysis.get('suggested_backend')}")
        elif re.search(r"\bci\b|\bgithub actions\b", criterion_lower):
            if analysis.get("suggested_backend") != "ci":
                errors.append(f"Expected CI backend, got {analysis.get('suggested_backend')}")
        
        # Check phased workflow detection (word-boundary)
        if re.search(r"\bphase\b|\bdependency\b|\bdepends on\b|\bsynthesis\b", criterion_lower):
            if not analysis.get("phased_workflow"):
                errors.append("Expected phased workflow detection")
    
    return len(errors) == 0, errors


def evaluate_case_dry_run(case: dict, analysis: dict) -> EvalResult:
    """Evaluate a test case in dry-run mode (no actual execution)."""
    case_id = case["id"]
    case_name = case["name"]
    criteria = case.get("evaluation_criteria", {})
    expected = case.get("expected_behavior", [])
    
    result = EvalResult(
        case_id=case_id,
        case_name=case_name,
        passed=True,
        score=0.0,
    )
    
    # Validate decomposition
    decomp_valid, decomp_errors = validate_decomposition(analysis, expected)
    result.criteria_results["decomposition_correct"] = decomp_valid
    if not decomp_valid:
        result.errors.extend(decomp_errors)
    
    # Check backend selection
    backend_criterion = criteria.get("backend_selection", "")
    if "docker" in backend_criterion.lower():
        result.criteria_results["backend_selection"] = analysis.get("suggested_backend") == "docker"
    elif "kubernetes" in backend_criterion.lower() or "k8s" in backend_criterion.lower():
        result.criteria_results["backend_selection"] = analysis.get("suggested_backend") == "k8s"
    elif "ci" in backend_criterion.lower():
        result.criteria_results["backend_selection"] = analysis.get("suggested_backend") == "ci"
    else:
        result.criteria_results["backend_selection"] = True  # Default pass
    
    # Check sharding if applicable (use tag-based check, not substring)
    case_tags = case.get("tags", [])
    if "sharding" in case_tags:
        result.criteria_results["sharding_strategy"] = analysis.get("sharding_applicable", False)
    
    # Check phased workflow if applicable (use tag-based check)
    if "phased-execution" in case_tags or "dependencies" in case_tags or "fan-out-fan-in" in case_tags:
        result.criteria_results["phases_identified"] = analysis.get("phased_workflow", False)
    
    # Calculate score
    passed_criteria = sum(1 for v in result.criteria_results.values() if v)
    total_criteria = len(result.criteria_results)
    result.score = passed_criteria / total_criteria if total_criteria > 0 else 0.0
    result.passed = result.score >= 0.8  # 80% threshold from evals.json
    
    return result


def run_dry_run_eval(evals_data: dict, case_filter: Optional[str] = None) -> list[EvalResult]:
    """Run all evaluations in dry-run mode."""
    results = []
    test_cases = evals_data.get("test_cases", [])
    
    for case in test_cases:
        if case_filter and case["id"] != case_filter:
            continue
        
        prompt = case.get("prompt", "")
        analysis = analyze_prompt_for_decomposition(prompt)
        result = evaluate_case_dry_run(case, analysis)
        results.append(result)
    
    return results


def print_results(results: list[EvalResult], verbose: bool = False) -> int:
    """Print evaluation results and return exit code."""
    total = len(results)
    num_passed = sum(1 for r in results if r.passed)
    
    print(f"\n{'='*60}")
    print("Multi-Agent Workflows Eval Results")
    print(f"{'='*60}\n")
    
    for result in results:
        status = "✓ PASS" if result.passed else "✗ FAIL"
        print(f"{status} [{result.case_id}] {result.case_name}")
        print(f"     Score: {result.score:.1%}")
        
        if verbose or not result.passed:
            for criterion, criterion_passed in result.criteria_results.items():
                mark = "✓" if criterion_passed else "✗"
                print(f"     {mark} {criterion}")
            
            for error in result.errors:
                print(f"     ⚠ {error}")
        
        print()
    
    print(f"{'='*60}")
    print(f"Total: {num_passed}/{total} passed ({num_passed/total*100:.0f}%)")
    print(f"{'='*60}")
    
    return 0 if num_passed == total else 1


def main():
    parser = argparse.ArgumentParser(
        description="Run multi-agent-workflows evaluations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Validate without spawning agents (default mode)",
    )
    parser.add_argument(
        "--integration", "-i",
        action="store_true",
        help="Run full integration tests (requires Docker)",
    )
    parser.add_argument(
        "--case", "-c",
        metavar="ID",
        help="Run specific test case by ID",
    )
    parser.add_argument(
        "--evals-file",
        type=Path,
        default=Path(__file__).parent.parent / "evals" / "evals.json",
        help="Path to evals.json file",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output for all cases",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    
    args = parser.parse_args()
    
    # Load evals
    evals_data = load_evals(args.evals_file)
    
    # Run evaluations
    if args.integration:
        print("Integration mode not yet implemented", file=sys.stderr)
        print("Use --dry-run for CI validation", file=sys.stderr)
        sys.exit(1)
    else:
        # Default to dry-run
        results = run_dry_run_eval(evals_data, args.case)
    
    # Output results
    if args.json:
        output = {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "results": [
                {
                    "case_id": r.case_id,
                    "case_name": r.case_name,
                    "passed": r.passed,
                    "score": r.score,
                    "criteria": r.criteria_results,
                    "errors": r.errors,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
        sys.exit(0 if output["passed"] == output["total"] else 1)
    else:
        sys.exit(print_results(results, args.verbose))


if __name__ == "__main__":
    main()
