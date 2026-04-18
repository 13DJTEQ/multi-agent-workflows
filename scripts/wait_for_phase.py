#!/usr/bin/env python3
"""
Wait for agent phases to complete.

Monitors Docker containers or Kubernetes Jobs and waits for all agents
in a specific phase to complete before continuing.

Usage:
    python3 wait_for_phase.py --phase 1 --backend docker
    python3 wait_for_phase.py --phase analysis --backend k8s --namespace warp-agents
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class Backend(Enum):
    DOCKER = "docker"
    KUBERNETES = "k8s"


class Status(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class AgentStatus:
    agent_id: str
    status: Status
    exit_code: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


def get_docker_agents(phase: str) -> list[str]:
    """Get list of Docker containers matching phase."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=agent-{phase}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [name.strip() for name in result.stdout.splitlines() if name.strip()]
    except Exception:
        return []


def get_docker_status(container_name: str) -> AgentStatus:
    """Get status of a Docker container."""
    try:
        result = subprocess.run(
            ["docker", "inspect", container_name, "--format", 
             "{{.State.Status}},{{.State.ExitCode}},{{.State.StartedAt}},{{.State.FinishedAt}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        parts = result.stdout.strip().split(",")
        state, exit_code, started, finished = parts[0], int(parts[1]), parts[2], parts[3]
        
        if state == "running":
            status = Status.RUNNING
        elif state == "exited":
            status = Status.COMPLETED if exit_code == 0 else Status.FAILED
        elif state == "created":
            status = Status.PENDING
        else:
            status = Status.UNKNOWN
        
        return AgentStatus(
            agent_id=container_name,
            status=status,
            exit_code=exit_code if state == "exited" else None,
            start_time=datetime.fromisoformat(started.replace("Z", "+00:00")) if started else None,
            end_time=datetime.fromisoformat(finished.replace("Z", "+00:00")) if finished and state == "exited" else None,
        )
    except Exception as e:
        return AgentStatus(agent_id=container_name, status=Status.UNKNOWN)


def get_k8s_jobs(phase: str, namespace: str) -> list[str]:
    """Get list of Kubernetes Jobs matching phase."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "jobs", "-n", namespace, "-l", f"phase={phase}", "-o", "jsonpath={.items[*].metadata.name}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.split()
    except Exception:
        # Try matching by name pattern
        try:
            result = subprocess.run(
                ["kubectl", "get", "jobs", "-n", namespace, "-o", "jsonpath={.items[*].metadata.name}"],
                capture_output=True,
                text=True,
                check=True,
            )
            all_jobs = result.stdout.split()
            return [j for j in all_jobs if f"phase-{phase}" in j or f"-{phase}-" in j]
        except Exception:
            return []


def get_k8s_status(job_name: str, namespace: str) -> AgentStatus:
    """Get status of a Kubernetes Job."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        job = json.loads(result.stdout)
        
        conditions = job.get("status", {}).get("conditions", [])
        active = job.get("status", {}).get("active", 0)
        succeeded = job.get("status", {}).get("succeeded", 0)
        failed = job.get("status", {}).get("failed", 0)
        
        if succeeded > 0:
            status = Status.COMPLETED
        elif failed > 0:
            status = Status.FAILED
        elif active > 0:
            status = Status.RUNNING
        else:
            status = Status.PENDING
        
        start_time = None
        if "startTime" in job.get("status", {}):
            start_time = datetime.fromisoformat(job["status"]["startTime"].replace("Z", "+00:00"))
        
        completion_time = None
        if "completionTime" in job.get("status", {}):
            completion_time = datetime.fromisoformat(job["status"]["completionTime"].replace("Z", "+00:00"))
        
        return AgentStatus(
            agent_id=job_name,
            status=status,
            exit_code=0 if succeeded > 0 else (1 if failed > 0 else None),
            start_time=start_time,
            end_time=completion_time,
        )
    except Exception:
        return AgentStatus(agent_id=job_name, status=Status.UNKNOWN)


def wait_for_agents(
    agents: list[str],
    get_status_fn,
    timeout: int,
    poll_interval: int,
    fail_fast: bool,
) -> tuple[list[AgentStatus], bool]:
    """Wait for all agents to complete."""
    start = time.time()
    final_statuses = {}
    
    while time.time() - start < timeout:
        all_complete = True
        has_failure = False
        
        for agent in agents:
            if agent in final_statuses:
                continue
            
            status = get_status_fn(agent)
            
            if status.status in (Status.COMPLETED, Status.FAILED):
                final_statuses[agent] = status
                if status.status == Status.FAILED:
                    has_failure = True
                    print(f"✗ Failed: {agent}", file=sys.stderr)
                else:
                    print(f"✓ Completed: {agent}", file=sys.stderr)
            else:
                all_complete = False
        
        if all_complete:
            return list(final_statuses.values()), True
        
        if fail_fast and has_failure:
            # Get final status for remaining agents
            for agent in agents:
                if agent not in final_statuses:
                    final_statuses[agent] = get_status_fn(agent)
            return list(final_statuses.values()), False
        
        # Show progress
        completed = len(final_statuses)
        print(f"  Progress: {completed}/{len(agents)} ({(time.time() - start):.0f}s)", file=sys.stderr, end="\r")
        
        time.sleep(poll_interval)
    
    # Timeout - get final status for all agents
    for agent in agents:
        if agent not in final_statuses:
            final_statuses[agent] = get_status_fn(agent)
    
    return list(final_statuses.values()), False


def main():
    parser = argparse.ArgumentParser(description="Wait for agent phases to complete")
    
    # Phase selection
    parser.add_argument("--phase", required=True, help="Phase identifier to wait for")
    parser.add_argument("--depends-on", help="Previous phase that must complete first")
    
    # Backend options
    parser.add_argument("--backend", "-b", choices=["docker", "k8s"], default="docker", help="Execution backend")
    parser.add_argument("--namespace", "-n", default="warp-agents", help="Kubernetes namespace")
    
    # Wait options
    parser.add_argument("--timeout", "-t", type=int, default=3600, help="Timeout in seconds")
    parser.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds")
    parser.add_argument("--fail-fast", action="store_true", help="Exit immediately on first failure")
    
    # Output options
    parser.add_argument("--output", "-o", type=Path, help="Write status report to file")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # Success criteria
    parser.add_argument("--min-success", type=float, default=1.0, help="Minimum success ratio (0.0-1.0)")
    
    args = parser.parse_args()
    
    # If depends-on specified, wait for that phase first
    if args.depends_on:
        print(f"Checking dependency: phase {args.depends_on}...", file=sys.stderr)
        
        if args.backend == "docker":
            dep_agents = get_docker_agents(args.depends_on)
            get_status = get_docker_status
        else:
            dep_agents = get_k8s_jobs(args.depends_on, args.namespace)
            get_status = lambda j: get_k8s_status(j, args.namespace)
        
        if dep_agents:
            statuses, success = wait_for_agents(
                dep_agents, get_status, args.timeout, args.poll_interval, args.fail_fast
            )
            
            completed = sum(1 for s in statuses if s.status == Status.COMPLETED)
            if completed / len(statuses) < args.min_success:
                print(f"Error: Dependency phase {args.depends_on} did not meet success criteria", file=sys.stderr)
                sys.exit(1)
    
    # Get agents for this phase
    print(f"Waiting for phase: {args.phase}...", file=sys.stderr)
    
    if args.backend == "docker":
        agents = get_docker_agents(args.phase)
        get_status = get_docker_status
    else:
        agents = get_k8s_jobs(args.phase, args.namespace)
        get_status = lambda j: get_k8s_status(j, args.namespace)
    
    if not agents:
        print(f"Warning: No agents found for phase {args.phase}", file=sys.stderr)
        sys.exit(0)
    
    print(f"Found {len(agents)} agents", file=sys.stderr)
    
    # Wait for completion
    statuses, success = wait_for_agents(
        agents, get_status, args.timeout, args.poll_interval, args.fail_fast
    )
    
    # Calculate results
    completed = sum(1 for s in statuses if s.status == Status.COMPLETED)
    failed = sum(1 for s in statuses if s.status == Status.FAILED)
    pending = sum(1 for s in statuses if s.status in (Status.PENDING, Status.RUNNING))
    total = len(statuses)
    success_ratio = completed / total if total else 0
    
    # Build report
    report = {
        "phase": args.phase,
        "backend": args.backend,
        "total": total,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "success_ratio": success_ratio,
        "threshold_met": success_ratio >= args.min_success,
        "timeout": not success and pending > 0,
        "agents": [
            {
                "id": s.agent_id,
                "status": s.status.value,
                "exit_code": s.exit_code,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
            }
            for s in statuses
        ],
    }
    
    # Output
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nPhase {args.phase} Summary:", file=sys.stderr)
        print(f"  Total: {total}", file=sys.stderr)
        print(f"  Completed: {completed}", file=sys.stderr)
        print(f"  Failed: {failed}", file=sys.stderr)
        print(f"  Pending: {pending}", file=sys.stderr)
        print(f"  Success ratio: {success_ratio:.1%}", file=sys.stderr)
    
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        print(f"Report written to {args.output}", file=sys.stderr)
    
    # Exit code
    if success_ratio >= args.min_success:
        print(f"✓ Phase {args.phase} completed successfully", file=sys.stderr)
        sys.exit(0)
    else:
        print(f"✗ Phase {args.phase} failed (success ratio {success_ratio:.1%} < {args.min_success:.1%})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
