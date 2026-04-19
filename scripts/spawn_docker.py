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
import random
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Optional credential helper integration (same package)
try:
    from scripts.credential_helper import resolve_secret  # type: ignore
except ImportError:
    try:
        # When run as a module inside the package
        from .credential_helper import resolve_secret  # type: ignore
    except ImportError:
        resolve_secret = None  # type: ignore

# Preflight thresholds
MIN_FREE_DISK_MB = 500  # warn if output dir has less than this
DOCKER_DAEMON_TIMEOUT_SEC = 5


@dataclass
class AgentResult:
    """Result of spawning or completing an agent container."""
    task_id: str
    task: str
    container_id: str
    status: str  # "running", "completed", "failed"
    exit_code: Optional[int] = None
    error: Optional[str] = None
    retries: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    
    @property
    def duration_seconds(self) -> Optional[float]:
        """Calculate task duration if both times are set."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None


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
        exit_code_str = result.stdout.strip()
        if not exit_code_str:
            return -1, "Empty response from docker wait"
        exit_code = int(exit_code_str)
        return exit_code, ""
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "stop", task_id], capture_output=True)
        return -1, "Timeout"
    except ValueError as e:
        return -1, f"Invalid exit code: {e}"
    except Exception as e:
        return -1, str(e)


def calculate_backoff(retry: int, base_delay: float = 2.0, max_delay: float = 60.0) -> float:
    """Calculate exponential backoff delay with jitter."""
    delay = min(base_delay * (2 ** retry), max_delay)
    jitter = delay * 0.1 * random.random()
    return delay + jitter


def check_docker_available() -> tuple[bool, str]:
    """Verify the Docker daemon is reachable. Returns (ok, message)."""
    if not shutil.which("docker"):
        return False, "`docker` CLI not found in PATH. Install Docker Desktop or docker-ce."
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_DAEMON_TIMEOUT_SEC,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or "unknown error"
            if "Cannot connect" in stderr or "daemon" in stderr.lower():
                return False, f"Docker daemon not running: {stderr}"
            return False, f"docker info failed: {stderr}"
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"Docker daemon did not respond within {DOCKER_DAEMON_TIMEOUT_SEC}s (is it starting?)"
    except OSError as e:
        return False, f"Failed to invoke docker: {e}"


def check_disk_space(path: Path, min_free_mb: int = MIN_FREE_DISK_MB) -> tuple[bool, str]:
    """Verify `path` has enough free space. Returns (ok, message)."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        stat = shutil.disk_usage(path)
        free_mb = stat.free // (1024 * 1024)
        if free_mb < min_free_mb:
            return False, f"Only {free_mb}MB free at {path} (need >= {min_free_mb}MB). Clear disk or choose another --output-dir."
        return True, f"{free_mb}MB free at {path}"
    except OSError as e:
        if "No space left" in str(e):
            return False, f"Disk full at {path}: {e}"
        return False, f"Cannot access {path}: {e}"


def check_output_writable(path: Path) -> tuple[bool, str]:
    """Verify we can write to the output directory."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".writable-probe-{os.getpid()}"
        probe.write_text("ok")
        probe.unlink()
        return True, "writable"
    except OSError as e:
        return False, f"Cannot write to {path}: {e}"


def preflight_checks(output_dir: Path, skip_docker: bool = False) -> list[str]:
    """Run all preflight checks. Returns list of error messages (empty = OK)."""
    errors: list[str] = []

    if not skip_docker:
        ok, msg = check_docker_available()
        if ok:
            print(f"✓ Docker daemon reachable (server v{msg})", file=sys.stderr)
        else:
            errors.append(f"Docker: {msg}")

    ok, msg = check_output_writable(output_dir)
    if ok:
        print(f"✓ Output directory writable: {output_dir}", file=sys.stderr)
    else:
        errors.append(f"Output dir: {msg}")

    ok, msg = check_disk_space(output_dir)
    if ok:
        print(f"✓ Disk space OK ({msg})", file=sys.stderr)
    else:
        errors.append(f"Disk: {msg}")

    return errors


def get_container_logs(task_id: str) -> str:
    """Get logs from a container."""
    result = subprocess.run(
        ["docker", "logs", task_id],
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def check_circuit_breaker(results: list[AgentResult], threshold: float, min_samples: int = 3) -> bool:
    """Check if failure rate exceeds threshold.
    
    Args:
        results: List of completed agent results
        threshold: Failure rate threshold (0.0-1.0)
        min_samples: Minimum samples before triggering (avoids early false positives)
    """
    if len(results) < min_samples:
        return False
    failed = sum(1 for r in results if r.status == "failed")
    return (failed / len(results)) > threshold


def validate_tasks_file(path: Path) -> list[str]:
    """Validate and load tasks from file."""
    if not path.exists():
        print(f"Error: Tasks file not found: {path}", file=sys.stderr)
        sys.exit(1)
    tasks = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not tasks:
        print(f"Error: No tasks found in {path}", file=sys.stderr)
        sys.exit(1)
    return tasks


def main():
    parser = argparse.ArgumentParser(
        description="Spawn parallel agents in Docker containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --tasks "Analyze file.py" "Test module.py" --wait --json
  %(prog)s --tasks-file tasks.txt --phase analysis --parallel 8
  %(prog)s --tasks "Run linter" --docker-args "--gpus all" --memory 8g
""",
    )
    
    # Task input (mutually exclusive)
    task_group = parser.add_argument_group("Task Input (one required)")
    task_group.add_argument("--tasks", nargs="+", metavar="PROMPT", help="Task prompts to execute")
    task_group.add_argument("--tasks-file", type=Path, metavar="FILE", help="File with tasks (one per line, # comments allowed)")
    
    # Container configuration
    container_group = parser.add_argument_group("Container Configuration")
    container_group.add_argument("--image", default="warpdotdev/dev-base:latest", help="Docker image (default: %(default)s)")
    container_group.add_argument("--workspace", type=Path, default=Path.cwd(), metavar="DIR", help="Workspace directory to mount")
    container_group.add_argument("--output-dir", type=Path, default=Path("./outputs"), metavar="DIR", help="Output directory (default: %(default)s)")
    container_group.add_argument("--memory", default="4g", help="Memory limit per container (default: %(default)s)")
    container_group.add_argument("--cpus", default="2", help="CPU limit per container (default: %(default)s)")
    container_group.add_argument("--network", metavar="NAME", help="Docker network name")
    container_group.add_argument("--docker-args", metavar="ARGS", help="Extra Docker run arguments (quoted string)")
    container_group.add_argument("--env", nargs="+", metavar="KEY=VALUE", help="Extra environment variables")
    
    # Execution control
    exec_group = parser.add_argument_group("Execution Control")
    exec_group.add_argument("--parallel", type=int, default=4, metavar="N", help="Max parallel containers (default: %(default)s)")
    exec_group.add_argument("--timeout", type=int, default=3600, metavar="SEC", help="Timeout per task in seconds (default: %(default)s)")
    exec_group.add_argument("--phase", metavar="ID", help="Phase identifier for multi-phase workflows")
    exec_group.add_argument("--share", default="team", choices=["team", "public", "private"], help="Session sharing mode (default: %(default)s)")
    
    # Fault tolerance
    fault_group = parser.add_argument_group("Fault Tolerance")
    fault_group.add_argument("--circuit-breaker", type=float, default=0.5, metavar="RATIO", help="Stop if failure rate exceeds threshold (default: %(default)s)")
    fault_group.add_argument("--retry-failed", action="store_true", help="Retry failed tasks with exponential backoff")
    fault_group.add_argument("--max-retries", type=int, default=3, metavar="N", help="Max retries per task (default: %(default)s)")
    fault_group.add_argument("--skip-preflight", action="store_true", help="Skip docker/disk preflight checks (not recommended)")
    fault_group.add_argument("--credential-backend", choices=["env", "keychain", "1password", "vault", "aws"], help="Backend for resolving WARP_API_KEY (default: env then keychain)")
    
    # Output options
    out_group = parser.add_argument_group("Output")
    out_group.add_argument("--wait", action="store_true", help="Wait for all containers to complete")
    out_group.add_argument("--json", action="store_true", help="Output results as JSON (includes metrics)")
    
    args = parser.parse_args()
    
    # Get API key via credential helper (with env fallback)
    api_key: Optional[str] = None
    if resolve_secret is not None:
        try:
            api_key = resolve_secret("WARP_API_KEY", backend=args.credential_backend)
        except Exception as e:
            print(f"Warning: credential backend failed ({e}); falling back to env", file=sys.stderr)
    if not api_key:
        api_key = os.environ.get("WARP_API_KEY")
    if not api_key:
        print(
            "Error: WARP_API_KEY not found. Set it via one of:\n"
            "  - export WARP_API_KEY=...\n"
            "  - python3 scripts/credential_helper.py set WARP_API_KEY\n"
            "  - --credential-backend 1password (with `op` signed in)",
            file=sys.stderr,
        )
        sys.exit(1)
    
    # Preflight checks (docker daemon, disk space, output writable)
    preflight_errors = preflight_checks(args.output_dir.resolve(), skip_docker=args.skip_preflight)
    if preflight_errors:
        print("\n✗ Preflight checks failed:", file=sys.stderr)
        for e in preflight_errors:
            print(f"  - {e}", file=sys.stderr)
        print("\nRun with --skip-preflight to bypass (not recommended).", file=sys.stderr)
        sys.exit(1)
    
    # Get tasks
    if args.tasks:
        tasks = args.tasks
    elif args.tasks_file:
        tasks = validate_tasks_file(args.tasks_file)
    else:
        parser.error("Must provide --tasks or --tasks-file")
    
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
    spawn_start = time.time()
    
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
                result.start_time = time.time()
                exit_code, error = wait_for_container(result.task_id, args.timeout)
                result.end_time = time.time()
                result.exit_code = exit_code
                
                if exit_code == 0:
                    result.status = "completed"
                    print(f"✓ Completed: {result.task_id} ({result.duration_seconds:.1f}s)", file=sys.stderr)
                else:
                    result.status = "failed"
                    result.error = error or f"Exit code: {exit_code}"
                    print(f"✗ Failed: {result.task_id} - {result.error}", file=sys.stderr)
                    
                    # Retry logic with exponential backoff
                    if args.retry_failed:
                        for retry in range(args.max_retries):
                            backoff = calculate_backoff(retry)
                            print(f"  Retrying ({retry + 1}/{args.max_retries}) after {backoff:.1f}s...", file=sys.stderr)
                            time.sleep(backoff)
                            
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
                                    result.retries = retry + 1
                                    print(f"  ✓ Retry succeeded", file=sys.stderr)
                                    break
    
    # Output results
    spawn_duration = time.time() - spawn_start
    
    if args.json:
        completed_durations = [r.duration_seconds for r in results if r.duration_seconds]
        output = {
            "total": len(results),
            "running": sum(1 for r in results if r.status == "running"),
            "completed": sum(1 for r in results if r.status == "completed"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "total_retries": sum(r.retries for r in results),
            "metrics": {
                "spawn_duration_seconds": spawn_duration,
                "avg_task_duration_seconds": sum(completed_durations) / len(completed_durations) if completed_durations else None,
                "max_task_duration_seconds": max(completed_durations) if completed_durations else None,
            },
            "results": [
                {
                    "task_id": r.task_id,
                    "task": r.task,
                    "container_id": r.container_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "error": r.error,
                    "retries": r.retries,
                    "duration_seconds": r.duration_seconds,
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
