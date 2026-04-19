#!/usr/bin/env python3
"""
Dependency-aware task ordering for multi-agent workflows.

Reads a task manifest (JSON or YAML) with explicit `depends_on` edges, produces
an optimized execution plan via Kahn's topological sort, and emits either:

    - A sequence of phases (each phase's tasks can run in parallel)
    - A machine-readable plan (JSON) for spawn scripts to consume
    - A Graphviz DOT visualization

Manifest format:
    # manifest.yaml
    tasks:
      - id: scan
        prompt: "Scan codebase for issues"
      - id: deep-security
        prompt: "Deep security review"
        depends_on: [scan]
      - id: deep-perf
        prompt: "Deep performance review"
        depends_on: [scan]
      - id: synthesize
        prompt: "Synthesize findings"
        depends_on: [deep-security, deep-perf]

Usage:
    python3 dependency_graph.py plan manifest.yaml
    python3 dependency_graph.py plan manifest.json --format json
    python3 dependency_graph.py validate manifest.yaml
    python3 dependency_graph.py dot manifest.yaml > graph.dot

Optimized ordering:
    - Tasks in the same phase have no dependencies on each other and can run
      in parallel. This yields the minimum number of synchronization points.
    - Tasks are topologically sorted; within a phase, they're sorted by id
      for deterministic output.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Task:
    id: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    backend: Optional[str] = None  # docker, k8s, ssh, ci
    env: dict[str, str] = field(default_factory=dict)
    resources: dict[str, str] = field(default_factory=dict)  # memory, cpus


def load_manifest(path: Path) -> list[Task]:
    """Load a task manifest from JSON or YAML."""
    text = path.read_text()
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError:
            raise RuntimeError("YAML support requires `pip install pyyaml`. Convert to JSON or install pyyaml.")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if not isinstance(data, dict) or "tasks" not in data:
        raise ValueError("Manifest must be a dict with 'tasks' key")

    tasks = []
    for raw in data["tasks"]:
        if "id" not in raw or "prompt" not in raw:
            raise ValueError(f"Task missing required fields (id, prompt): {raw}")
        tasks.append(
            Task(
                id=raw["id"],
                prompt=raw["prompt"],
                depends_on=list(raw.get("depends_on", [])),
                backend=raw.get("backend"),
                env=dict(raw.get("env", {})),
                resources=dict(raw.get("resources", {})),
            )
        )
    return tasks


def validate(tasks: list[Task]) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors: list[str] = []
    ids = [t.id for t in tasks]
    id_set = set(ids)

    # Duplicate IDs
    if len(ids) != len(id_set):
        seen = set()
        for i in ids:
            if i in seen:
                errors.append(f"Duplicate task id: {i!r}")
            seen.add(i)

    # Unknown dependencies
    for t in tasks:
        for dep in t.depends_on:
            if dep not in id_set:
                errors.append(f"Task {t.id!r} depends on unknown task {dep!r}")
            if dep == t.id:
                errors.append(f"Task {t.id!r} depends on itself")

    # Cycle detection via Kahn's (if topo sort fails, there's a cycle)
    try:
        topo_sort(tasks)
    except ValueError as e:
        errors.append(str(e))

    return errors


def topo_sort(tasks: list[Task]) -> list[list[Task]]:
    """Kahn's algorithm producing phases (each phase runs in parallel).

    Returns a list of phases. Tasks within a phase are sorted by id for
    deterministic output.

    Raises ValueError if a cycle is detected.
    """
    by_id = {t.id: t for t in tasks}

    # Build indegree + adjacency
    indeg: dict[str, int] = {t.id: 0 for t in tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        for dep in t.depends_on:
            indeg[t.id] += 1
            children[dep].append(t.id)

    phases: list[list[Task]] = []
    remaining = dict(indeg)
    while remaining:
        # Current phase: all tasks with indegree 0
        current = sorted([tid for tid, d in remaining.items() if d == 0])
        if not current:
            unresolved = sorted(remaining.keys())
            raise ValueError(f"Cycle detected among tasks: {unresolved}")
        phases.append([by_id[tid] for tid in current])
        # Remove current from remaining and decrement children
        for tid in current:
            for child in children[tid]:
                if child in remaining:
                    remaining[child] -= 1
            del remaining[tid]

    return phases


def plan_to_dict(phases: list[list[Task]]) -> dict[str, Any]:
    """Serialize a plan to a dict (JSON-ready)."""
    return {
        "num_phases": len(phases),
        "total_tasks": sum(len(p) for p in phases),
        "phases": [
            {
                "phase": i + 1,
                "parallelism": len(p),
                "tasks": [
                    {
                        "id": t.id,
                        "prompt": t.prompt,
                        "depends_on": t.depends_on,
                        "backend": t.backend,
                        "env": t.env,
                        "resources": t.resources,
                    }
                    for t in p
                ],
            }
            for i, p in enumerate(phases)
        ],
    }


def format_text(phases: list[list[Task]]) -> str:
    """Human-readable plan."""
    lines = [f"Plan: {len(phases)} phases, {sum(len(p) for p in phases)} tasks", ""]
    for i, phase in enumerate(phases, start=1):
        lines.append(f"Phase {i} (parallelism={len(phase)}):")
        for t in phase:
            deps = f" [depends_on: {', '.join(t.depends_on)}]" if t.depends_on else ""
            lines.append(f"  - {t.id}: {t.prompt}{deps}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_dot(tasks: list[Task], phases: list[list[Task]]) -> str:
    """Graphviz DOT for visualization."""
    lines = ["digraph tasks {", "  rankdir=LR;", "  node [shape=box, style=rounded];"]
    # Rank each phase
    for i, phase in enumerate(phases, start=1):
        lines.append(f"  subgraph cluster_{i} {{")
        lines.append(f'    label="Phase {i}";')
        lines.append("    style=dashed;")
        for t in phase:
            label = f'{t.id}\\n{t.prompt[:40]}'
            lines.append(f'    "{t.id}" [label="{label}"];')
        lines.append("  }")
    # Edges
    for t in tasks:
        for dep in t.depends_on:
            lines.append(f'  "{dep}" -> "{t.id}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def cmd_plan(args: argparse.Namespace) -> int:
    tasks = load_manifest(args.manifest)
    errors = validate(tasks)
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        return 1

    phases = topo_sort(tasks)

    if args.format == "json":
        print(json.dumps(plan_to_dict(phases), indent=2))
    elif args.format == "text":
        print(format_text(phases), end="")
    elif args.format == "phase-commands":
        # Emit shell commands to drive spawn_docker.py phase-by-phase
        for i, phase in enumerate(phases, start=1):
            task_args = " ".join(repr(t.prompt) for t in phase)
            print(f"# Phase {i}")
            print(f"python3 scripts/spawn_docker.py --phase {i} --tasks {task_args} --wait")
            if i < len(phases):
                print(f"python3 scripts/wait_for_phase.py --phase {i} --backend docker --min-success 1.0")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    tasks = load_manifest(args.manifest)
    errors = validate(tasks)
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"OK: {len(tasks)} tasks, {len(topo_sort(tasks))} phases")
    return 0


def cmd_dot(args: argparse.Namespace) -> int:
    tasks = load_manifest(args.manifest)
    errors = validate(tasks)
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    phases = topo_sort(tasks)
    print(format_dot(tasks, phases), end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dependency-aware task ordering for multi-agent workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s plan manifest.yaml
  %(prog)s plan manifest.json --format json | jq
  %(prog)s plan manifest.yaml --format phase-commands > run.sh
  %(prog)s validate manifest.yaml
  %(prog)s dot manifest.yaml | dot -Tsvg -o graph.svg
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Compute execution plan")
    p_plan.add_argument("manifest", type=Path)
    p_plan.add_argument(
        "--format",
        choices=["text", "json", "phase-commands"],
        default="text",
    )
    p_plan.set_defaults(func=cmd_plan)

    p_val = sub.add_parser("validate", help="Validate manifest (dependencies, cycles)")
    p_val.add_argument("manifest", type=Path)
    p_val.set_defaults(func=cmd_validate)

    p_dot = sub.add_parser("dot", help="Emit Graphviz DOT visualization")
    p_dot.add_argument("manifest", type=Path)
    p_dot.set_defaults(func=cmd_dot)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
