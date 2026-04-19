#!/usr/bin/env python3
"""
Spawn parallel agents on Warp's Oz cloud platform.

Fans out tasks via `oz agent run-cloud`, optionally waits for them to reach
a terminal state, and emits structured per-run results (including artifact
URLs when agents produce PRs).

Usage:
    python3 spawn_oz.py --tasks "t1" "t2" --environment ENV_ID --wait --json
    python3 spawn_oz.py --tasks-file tasks.txt --environment ENV_ID --parallel 8

Design notes:
    - `--environment` is **required**. Oz cloud agents have no implicit env.
    - No Docker-style flags (--image, --memory, --cpus, --network). Those are
      managed via `oz environment` and are deliberately out of scope here.
    - Credentials default to `env` backend: cloud agents inherit Oz-provisioned
      secrets as env vars at runtime. On the admin Mac, spawn_oz itself just
      needs `WARP_API_KEY` to call the `oz` CLI.
    - `--wait` blocks per-run via `oz run get` polling. Without it, spawn_oz
      returns immediately after launching all runs (fire-and-forget, useful
      for scheduled workflows).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Shared helpers
try:
    from scripts._common import calculate_backoff, check_circuit_breaker, validate_tasks_file  # type: ignore
except ImportError:
    try:
        from ._common import calculate_backoff, check_circuit_breaker, validate_tasks_file  # type: ignore
    except ImportError:
        # Fallback: direct script execution without package context
        sys.path.insert(0, str(Path(__file__).parent))
        from _common import calculate_backoff, check_circuit_breaker, validate_tasks_file  # type: ignore

try:
    from scripts.credential_helper import resolve_secret  # type: ignore
except ImportError:
    try:
        from .credential_helper import resolve_secret  # type: ignore
    except ImportError:
        resolve_secret = None  # type: ignore


OZ_TERMINAL_STATES = {"succeeded", "failed", "cancelled", "completed", "errored"}
OZ_RUN_GET_POLL_SEC = 5.0
OZ_RUN_GET_MAX_WAIT_SEC = 3600


@dataclass
class OzAgentResult:
    """Result of launching (and optionally waiting on) an Oz cloud agent."""

    task_id: str
    task: str
    run_id: str
    status: str  # "running", "succeeded", "failed", "cancelled"
    error: Optional[str] = None
    retries: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    output: Optional[str] = None
    pr_url: Optional[str] = None
    branch: Optional[str] = None
    raw_run_get: Optional[dict] = field(default=None, repr=False)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    def to_envelope(self) -> dict:
        """Emit in the common result envelope (references/result-schema.md)."""
        # Map internal Oz states to envelope-canonical status.
        status = "ok" if self.status in {"succeeded", "completed"} else (
            "failed" if self.status in {"failed", "cancelled", "errored"} else "partial"
        )
        env: dict = {
            "schema_version": "1",
            "status": status,
            "task_id": self.task_id,
            "data": {
                "run_id": self.run_id,
                "output": self.output,
                "pr_url": self.pr_url,
                "branch": self.branch,
            },
        }
        if self.error:
            env["error"] = self.error
        if self.duration_seconds is not None:
            env["metrics"] = {"duration_seconds": self.duration_seconds}
        return env


def generate_task_id(task: str, index: int, phase: Optional[str] = None) -> str:
    """Generate a run-naming slug. Oz auto-assigns run_id server-side, this is
    purely a client-side handle used in the output envelope."""
    safe_name = task.lower().replace(" ", "-").replace("_", "-")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c == "-")
    if phase:
        return f"ozagent-{phase}-{index}-{safe_name[:25]}"
    return f"ozagent-{index}-{safe_name[:30]}"


def check_oz_available() -> tuple[bool, str]:
    """Preflight: oz CLI installed and WARP_API_KEY reachable."""
    if not any(
        os.access(os.path.join(p, "oz"), os.X_OK)
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p
    ):
        return False, "`oz` CLI not found in PATH. Install Warp CLI: https://docs.warp.dev/reference/cli"
    # `oz` returns 0 for help even without auth; real check happens at spawn time.
    try:
        result = subprocess.run(
            ["oz", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, f"oz --version failed: {result.stderr.strip()}"
        return True, result.stdout.strip() or "oz CLI available"
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"Failed to invoke oz: {e}"


def spawn_oz_agent(
    task: str,
    task_id: str,
    environment: str,
    extra_env: Optional[dict] = None,
) -> OzAgentResult:
    """Spawn a single Oz cloud agent run. Returns immediately after launch."""
    cmd = [
        "oz",
        "agent",
        "run-cloud",
        "--prompt",
        task,
        "--environment",
        environment,
        "--output-format",
        "json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        return OzAgentResult(
            task_id=task_id,
            task=task,
            run_id="",
            status="failed",
            error=(e.stderr or str(e)).strip(),
        )
    except FileNotFoundError:
        return OzAgentResult(
            task_id=task_id,
            task=task,
            run_id="",
            status="failed",
            error="`oz` CLI not found in PATH",
        )

    run_id = _parse_run_id(proc.stdout)
    if not run_id:
        return OzAgentResult(
            task_id=task_id,
            task=task,
            run_id="",
            status="failed",
            error=f"Could not parse run_id from oz output: {proc.stdout[:200]!r}",
        )
    return OzAgentResult(
        task_id=task_id,
        task=task,
        run_id=run_id,
        status="running",
        start_time=time.time(),
    )


def _parse_run_id(stdout: str) -> Optional[str]:
    """Extract run_id from either JSON (preferred) or pretty/text output.

    Accepts:
      - {"run_id": "..."} or {"id": "...", ...}
      - "Spawned agent with run ID: <uuid>" (text format)
    """
    if not stdout:
        return None
    # JSON first
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            for k in ("run_id", "id", "runId", "run"):
                if k in data and isinstance(data[k], str):
                    return data[k]
    except (json.JSONDecodeError, ValueError):
        pass
    # Text fallback: look for a UUID-shaped token
    m = re.search(
        r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        stdout,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def poll_run(run_id: str) -> dict:
    """Fetch current state of a run via `oz run get`. Returns parsed JSON."""
    proc = subprocess.run(
        ["oz", "run", "get", run_id, "--output-format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"status": "unknown", "error": proc.stderr.strip()}
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        return {"status": "unknown", "error": f"malformed JSON: {e}"}


def wait_for_run(
    result: OzAgentResult,
    poll_sec: float = OZ_RUN_GET_POLL_SEC,
    max_wait_sec: int = OZ_RUN_GET_MAX_WAIT_SEC,
) -> OzAgentResult:
    """Block until the run reaches a terminal state or we time out."""
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        payload = poll_run(result.run_id)
        state = str(payload.get("status") or payload.get("state") or "").lower()
        if state in OZ_TERMINAL_STATES:
            result.end_time = time.time()
            result.status = state
            result.raw_run_get = payload
            result.output = payload.get("output") or payload.get("agent_output")
            # Look for PR artifact mentions in output
            if result.output:
                pr_match = re.search(
                    r"https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+",
                    result.output,
                )
                if pr_match:
                    result.pr_url = pr_match.group(0)
                branch_match = re.search(r"branch[:\s]+['\"]?([\w./\-]+)", result.output)
                if branch_match:
                    result.branch = branch_match.group(1)
            return result
        time.sleep(poll_sec)
    result.end_time = time.time()
    result.status = "failed"
    result.error = f"Timed out after {max_wait_sec}s waiting for run {result.run_id}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spawn parallel cloud agents on Warp's Oz platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --tasks "Summarize auth" "Review DB layer" --environment UA17BXYZ --wait --json
  %(prog)s --tasks-file tasks.txt --environment UA17BXYZ --parallel 8
""",
    )

    # Task input
    task_group = parser.add_argument_group("Task Input (one required)")
    task_group.add_argument("--tasks", nargs="+", metavar="PROMPT", help="Task prompts")
    task_group.add_argument("--tasks-file", type=Path, metavar="FILE", help="File with tasks (one per line)")

    # Oz config
    oz_group = parser.add_argument_group("Oz Configuration")
    oz_group.add_argument(
        "--environment",
        required=True,
        metavar="ENV_ID",
        help="Oz environment ID to run agents in (required). Use `oz environment list` to find yours.",
    )

    # Execution
    exec_group = parser.add_argument_group("Execution Control")
    exec_group.add_argument("--parallel", type=int, default=4, metavar="N", help="Max parallel spawns (default: %(default)s)")
    exec_group.add_argument("--timeout", type=int, default=OZ_RUN_GET_MAX_WAIT_SEC, metavar="SEC", help="Per-run wait timeout (default: %(default)s)")
    exec_group.add_argument("--poll-interval", type=float, default=OZ_RUN_GET_POLL_SEC, metavar="SEC", help="Polling interval when --wait (default: %(default)s)")
    exec_group.add_argument("--phase", metavar="ID", help="Phase identifier for multi-phase workflows")

    # Fault tolerance
    fault_group = parser.add_argument_group("Fault Tolerance")
    fault_group.add_argument("--circuit-breaker", type=float, default=0.5, metavar="RATIO", help="Stop spawning if failure rate exceeds threshold (default: %(default)s)")
    fault_group.add_argument("--retry-failed", action="store_true", help="Retry failed spawns with exponential backoff")
    fault_group.add_argument("--max-retries", type=int, default=3, metavar="N", help="Max retries per task (default: %(default)s)")
    fault_group.add_argument("--skip-preflight", action="store_true", help="Skip `oz --version` preflight")
    fault_group.add_argument(
        "--credential-backend",
        choices=["env", "keychain", "1password", "vault", "aws", "oz"],
        default="env",
        help="Backend for resolving WARP_API_KEY (default: env — cloud agents inherit secrets as env vars)",
    )

    # Output
    out_group = parser.add_argument_group("Output")
    out_group.add_argument("--wait", action="store_true", help="Wait for all runs to reach terminal state")
    out_group.add_argument("--json", action="store_true", help="Emit results as JSON envelope per task")
    out_group.add_argument("--output-dir", type=Path, default=Path("./outputs"), metavar="DIR", help="Directory to write per-run result.json (default: %(default)s)")

    args = parser.parse_args()

    # WARP_API_KEY sanity check (oz CLI needs it)
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
            "Error: WARP_API_KEY not found. Set via one of:\n"
            "  - export WARP_API_KEY=...\n"
            "  - python3 scripts/credential_helper.py set WARP_API_KEY\n"
            "  - Inside an Oz cloud agent, it is injected automatically",
            file=sys.stderr,
        )
        return 1

    # Preflight
    if not args.skip_preflight:
        ok, msg = check_oz_available()
        if not ok:
            print(f"✗ Preflight: {msg}", file=sys.stderr)
            print("Run with --skip-preflight to bypass.", file=sys.stderr)
            return 1
        print(f"✓ {msg}", file=sys.stderr)

    # Load tasks
    if args.tasks:
        tasks = args.tasks
    elif args.tasks_file:
        tasks = validate_tasks_file(args.tasks_file)
    else:
        parser.error("Must provide --tasks or --tasks-file")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    results: list[OzAgentResult] = []
    print(f"Spawning {len(tasks)} cloud agents in environment {args.environment}...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {}
        for i, task in enumerate(tasks):
            if check_circuit_breaker(results, args.circuit_breaker):
                print(
                    f"Circuit breaker tripped at {args.circuit_breaker * 100:.0f}% failure rate; halting spawn.",
                    file=sys.stderr,
                )
                break
            task_id = generate_task_id(task, i, args.phase)
            fut = pool.submit(spawn_oz_agent, task=task, task_id=task_id, environment=args.environment)
            futures[fut] = (task, task_id)

        for fut in as_completed(futures):
            task, task_id = futures[fut]
            r = fut.result()
            results.append(r)
            if r.status == "running":
                print(f"✓ Spawned: {task_id} (run={r.run_id}) — {task[:60]}", file=sys.stderr)
            else:
                print(f"✗ Spawn failed: {task_id} — {r.error}", file=sys.stderr)
                if args.retry_failed and r.retries < args.max_retries:
                    # Simple retry (sequential; keeps parallelism code simple)
                    for attempt in range(args.max_retries):
                        backoff = calculate_backoff(attempt)
                        time.sleep(backoff)
                        retry = spawn_oz_agent(task=task, task_id=task_id, environment=args.environment)
                        retry.retries = attempt + 1
                        if retry.status == "running":
                            results[-1] = retry
                            print(f"✓ Retry {attempt + 1} succeeded: {task_id}", file=sys.stderr)
                            break

    # Wait if requested
    if args.wait:
        print("\nWaiting for cloud agents to reach terminal state...", file=sys.stderr)
        for r in results:
            if r.status == "running":
                wait_for_run(r, poll_sec=args.poll_interval, max_wait_sec=args.timeout)
                sym = "✓" if r.status in {"succeeded", "completed"} else "✗"
                dur = f"{r.duration_seconds:.1f}s" if r.duration_seconds else "?"
                print(f"{sym} {r.task_id} [{r.status}] ({dur})", file=sys.stderr)

    # Persist envelopes
    for r in results:
        env = r.to_envelope()
        task_out = args.output_dir / r.task_id
        task_out.mkdir(parents=True, exist_ok=True)
        (task_out / "result.json").write_text(json.dumps(env, indent=2))

    # Stdout output
    if args.json:
        payload = {
            "tasks_total": len(tasks),
            "tasks_spawned": sum(1 for r in results if r.run_id),
            "tasks_succeeded": sum(1 for r in results if r.status in {"succeeded", "completed"}),
            "tasks_failed": sum(1 for r in results if r.status in {"failed", "cancelled", "errored"}),
            "results": [r.to_envelope() for r in results],
        }
        print(json.dumps(payload, indent=2))

    # Exit code: non-zero if any failures
    return 0 if all(r.status in {"succeeded", "completed", "running"} for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
