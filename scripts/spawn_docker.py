#!/usr/bin/env python3
"""
Spawn parallel agents in Docker containers.

Usage:
    python3 spawn_docker.py --tasks "task1" "task2" --workspace /path/to/workspace
    python3 spawn_docker.py --tasks-file tasks.txt --image warpdotdev/dev-base:latest
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AgentResult:
    task_id: str
    task: str
    container_id: str
    status: str  # "running", "completed", "failed"
    exit_code: Optional[int] = None
    error: Optional[str] = None


def generate_task_id(task: str, index: int, phase: Optional[str] = None) -> str:
    """Generate a safe container name from task."""
    safe_name = task.lower().replace(" ", "-").replace("_", "-")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c == "-")
    if phase:
        return f"agent-{phase}-{index}-{safe_name[:25]}"
    return f"agent-{index}-{safe_name[:30]}"


def spawn_container(
    task: str,
    task_id: str,
    image: str,
    workspace: Path,
    output_dir: Path,
    api_key: str,
    memory: str,
    cpus: str,
    network: Optional[str],
    share: str,
    extra_env: dict,
    docker_args: Optional[str] = None,
) -> AgentResult:
    """Spawn a single Docker container for an agent task."""
    
    # Create output directory for this task
    task_output = output_dir / task_id
    task_output.mkdir(parents=True, exist_ok=True)
    
    # Build docker command
    cmd = [
        "docker", "run", "-d",
        "--name", task_id,
        "-v", f"{workspace}:/workspace",
        "-v", f"{task_output}:/output",
        "-w", "/workspace",
        "-e", f"WARP_API_KEY={api_key}",
        "-e", f"TASK_ID={task_id}",
        "-e", "OUTPUT_DIR=/output",
    ]
    
    # Add extra environment variables
    for key, value in extra_env.items():
        cmd.extend(["-e", f"{key}={value}"])
    
    # Resource limits
    if memory:
        cmd.extend(["--memory", memory])
    if cpus:
        cmd.extend(["--cpus", cpus])
    
    # Network
    if network:
        cmd.extend(["--network", network])
    
    # Extra Docker arguments
    if docker_args:
        import shlex
        cmd.extend(shlex.split(docker_args))
    
    # Image and command
    cmd.append(image)
    cmd.extend([
        "oz", "agent", "run",
        "--prompt", f"{task}. Save results to /output/result.json",
        "--share", share,
    ])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()
        return AgentResult(
            task_id=task_id,
            task=task,
            container_id=container_id,
            status="running",
        )
    except subprocess.CalledProcessError as e:
        return AgentResult(
            task_id=task_id,
            task=task,
            container_id="",
            status="failed",
            error=e.stderr,
        )


def wait_for_container(task_id: str, timeout: int = 3600) -> tuple[int, str]:
    """Wait for a container to complete and return exit code."""
    try:
        result = subprocess.run(
            ["docker", "wait", task_id],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = int(result.stdout.strip())
        return exit_code, ""
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "stop", task_id], capture_output=True)
        return -1, "Timeout"
    except Exception as e:
        return -1, str(e)


def get_container_logs(task_id: str) -> str:
    """Get logs from a container."""
    result = subprocess.run(
        ["docker", "logs", task_id],
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def check_circuit_breaker(results: list[AgentResult], threshold: float) -> bool:
    """Check if failure rate exceeds threshold."""
    if not results:
        return False
    failed = sum(1 for r in results if r.status == "failed")
    return (failed / len(results)) > threshold


def main():
    parser = argparse.ArgumentParser(description="Spawn parallel Docker agents")
    parser.add_argument("--tasks", nargs="+", help="List of task prompts")
    parser.add_argument("--tasks-file", type=Path, help="File with tasks (one per line)")
    parser.add_argument("--image", default="warpdotdev/dev-base:latest", help="Docker image")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace directory")
    parser.add_argument("--output-dir", type=Path, default=Path("./outputs"), help="Output directory")
    parser.add_argument("--memory", default="4g", help="Memory limit per container")
    parser.add_argument("--cpus", default="2", help="CPU limit per container")
    parser.add_argument("--network", help="Docker network name")
    parser.add_argument("--share", default="team", choices=["team", "public", "private"], help="Session sharing")
    parser.add_argument("--parallel", type=int, default=4, help="Max parallel containers")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout per task (seconds)")
    parser.add_argument("--circuit-breaker", type=float, default=0.5, help="Stop if failure rate exceeds threshold")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed tasks")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per task")
    parser.add_argument("--env", nargs="+", help="Extra env vars (KEY=VALUE)")
    parser.add_argument("--phase", help="Phase identifier for multi-phase workflows")
    parser.add_argument("--docker-args", help="Extra Docker run arguments (quoted string)")
    parser.add_argument("--wait", action="store_true", help="Wait for all containers to complete")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    
    args = parser.parse_args()
    
    # Get API key
    api_key = os.environ.get("WARP_API_KEY")
    if not api_key:
        print("Error: WARP_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    # Get tasks
    tasks = []
    if args.tasks:
        tasks = args.tasks
    elif args.tasks_file:
        tasks = [line.strip() for line in args.tasks_file.read_text().splitlines() if line.strip()]
    else:
        print("Error: Must provide --tasks or --tasks-file", file=sys.stderr)
        sys.exit(1)
    
    # Parse extra environment variables
    extra_env = {}
    if args.env:
        for env in args.env:
            key, value = env.split("=", 1)
            extra_env[key] = value
    
    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Spawn containers
    results: list[AgentResult] = []
    
    print(f"Spawning {len(tasks)} agents...", file=sys.stderr)
    
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {}
        
        for i, task in enumerate(tasks):
            # Check circuit breaker
            if check_circuit_breaker(results, args.circuit_breaker):
                print(f"Circuit breaker triggered at {args.circuit_breaker*100}% failure rate", file=sys.stderr)
                break
            
            task_id = generate_task_id(task, i, args.phase)
            future = executor.submit(
                spawn_container,
                task=task,
                task_id=task_id,
                image=args.image,
                workspace=args.workspace.resolve(),
                output_dir=args.output_dir.resolve(),
                api_key=api_key,
                memory=args.memory,
                cpus=args.cpus,
                network=args.network,
                share=args.share,
                extra_env=extra_env,
                docker_args=args.docker_args,
            )
            futures[future] = (task, task_id)
        
        for future in as_completed(futures):
            task, task_id = futures[future]
            result = future.result()
            results.append(result)
            
            if result.status == "running":
                print(f"✓ Started: {task_id} ({task[:50]}...)", file=sys.stderr)
            else:
                print(f"✗ Failed to start: {task_id} - {result.error}", file=sys.stderr)
    
    # Wait for completion if requested
    if args.wait:
        print("\nWaiting for containers to complete...", file=sys.stderr)
        
        for result in results:
            if result.status == "running":
                exit_code, error = wait_for_container(result.task_id, args.timeout)
                result.exit_code = exit_code
                
                if exit_code == 0:
                    result.status = "completed"
                    print(f"✓ Completed: {result.task_id}", file=sys.stderr)
                else:
                    result.status = "failed"
                    result.error = error or f"Exit code: {exit_code}"
                    print(f"✗ Failed: {result.task_id} - {result.error}", file=sys.stderr)
                    
                    # Retry logic
                    if args.retry_failed:
                        for retry in range(args.max_retries):
                            print(f"  Retrying ({retry + 1}/{args.max_retries})...", file=sys.stderr)
                            # Remove failed container
                            subprocess.run(["docker", "rm", "-f", result.task_id], capture_output=True)
                            
                            # Respawn
                            new_result = spawn_container(
                                task=result.task,
                                task_id=f"{result.task_id}-retry{retry+1}",
                                image=args.image,
                                workspace=args.workspace.resolve(),
                                output_dir=args.output_dir.resolve(),
                                api_key=api_key,
                                memory=args.memory,
                                cpus=args.cpus,
                                network=args.network,
                                share=args.share,
                                extra_env=extra_env,
                            )
                            
                            if new_result.status == "running":
                                exit_code, error = wait_for_container(new_result.task_id, args.timeout)
                                if exit_code == 0:
                                    result.status = "completed"
                                    result.task_id = new_result.task_id
                                    print(f"  ✓ Retry succeeded", file=sys.stderr)
                                    break
    
    # Output results
    if args.json:
        output = {
            "total": len(results),
            "running": sum(1 for r in results if r.status == "running"),
            "completed": sum(1 for r in results if r.status == "completed"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "results": [
                {
                    "task_id": r.task_id,
                    "task": r.task,
                    "container_id": r.container_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "error": r.error,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\nSummary:", file=sys.stderr)
        print(f"  Total: {len(results)}", file=sys.stderr)
        print(f"  Running: {sum(1 for r in results if r.status == 'running')}", file=sys.stderr)
        print(f"  Completed: {sum(1 for r in results if r.status == 'completed')}", file=sys.stderr)
        print(f"  Failed: {sum(1 for r in results if r.status == 'failed')}", file=sys.stderr)
        
        # Print container IDs for monitoring
        if not args.wait:
            print("\nContainer IDs:", file=sys.stderr)
            for r in results:
                if r.container_id:
                    print(f"  {r.task_id}: {r.container_id[:12]}")


if __name__ == "__main__":
    main()
