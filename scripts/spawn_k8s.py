#!/usr/bin/env python3
"""
Spawn parallel agents as Kubernetes Jobs.

Usage:
    python3 spawn_k8s.py --tasks "task1" "task2" --namespace warp-agents
    python3 spawn_k8s.py --tasks-file tasks.txt --image warpdotdev/dev-base:latest
"""

import argparse
import json
import os
import subprocess
import sys
import time
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


@dataclass
class JobResult:
    task_id: str
    task: str
    job_name: str
    status: str  # "pending", "running", "completed", "failed"
    error: Optional[str] = None


def generate_job_name(task: str, index: int) -> str:
    """Generate a safe Kubernetes job name from task."""
    safe_name = task.lower().replace(" ", "-").replace("_", "-")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c == "-")
    # K8s names must be <= 63 chars and start/end with alphanumeric
    name = f"agent-{index}-{safe_name[:40]}"
    return name.strip("-")


def create_job_manifest(
    task: str,
    job_name: str,
    namespace: str,
    image: str,
    secret_name: str,
    pvc_name: Optional[str],
    memory_request: str,
    memory_limit: str,
    cpu_request: str,
    cpu_limit: str,
    share: str,
) -> dict:
    """Create a Kubernetes Job manifest."""
    
    volumes = []
    volume_mounts = []
    
    if pvc_name:
        volumes.append({
            "name": "workspace",
            "persistentVolumeClaim": {"claimName": pvc_name},
        })
        volume_mounts.append({
            "name": "workspace",
            "mountPath": "/workspace",
        })
    
    # Output volume (emptyDir)
    volumes.append({
        "name": "output",
        "emptyDir": {},
    })
    volume_mounts.append({
        "name": "output",
        "mountPath": "/output",
    })
    
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app": "warp-agent",
                "task-id": job_name,
            },
        },
        "spec": {
            "backoffLimit": 3,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {
                    "labels": {
                        "app": "warp-agent",
                        "task-id": job_name,
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [{
                        "name": "agent",
                        "image": image,
                        "workingDir": "/workspace" if pvc_name else "/tmp",
                        "command": ["oz", "agent", "run"],
                        "args": [
                            "--prompt", f"{task}. Save results to /output/result.json",
                            "--share", share,
                        ],
                        "env": [
                            {
                                "name": "WARP_API_KEY",
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": secret_name,
                                        "key": "WARP_API_KEY",
                                    },
                                },
                            },
                            {
                                "name": "TASK_ID",
                                "value": job_name,
                            },
                            {
                                "name": "OUTPUT_DIR",
                                "value": "/output",
                            },
                        ],
                        "volumeMounts": volume_mounts,
                        "resources": {
                            "requests": {
                                "memory": memory_request,
                                "cpu": cpu_request,
                            },
                            "limits": {
                                "memory": memory_limit,
                                "cpu": cpu_limit,
                            },
                        },
                    }],
                    "volumes": volumes,
                },
            },
        },
    }
    
    return manifest


def apply_manifest(manifest: dict) -> tuple[bool, str]:
    """Apply a Kubernetes manifest."""
    try:
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=yaml.dump(manifest),
            capture_output=True,
            text=True,
            check=True,
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr


def get_job_status(job_name: str, namespace: str) -> str:
    """Get the status of a Kubernetes Job."""
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "job", job_name,
                "-n", namespace,
                "-o", "jsonpath={.status.conditions[?(@.type=='Complete')].status},{.status.conditions[?(@.type=='Failed')].status},{.status.active}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        complete, failed, active = result.stdout.split(",")
        
        if complete == "True":
            return "completed"
        elif failed == "True":
            return "failed"
        elif active:
            return "running"
        else:
            return "pending"
    except Exception:
        return "unknown"


def wait_for_job(job_name: str, namespace: str, timeout: int) -> str:
    """Wait for a job to complete."""
    start = time.time()
    while time.time() - start < timeout:
        status = get_job_status(job_name, namespace)
        if status in ("completed", "failed"):
            return status
        time.sleep(5)
    return "timeout"


def get_job_logs(job_name: str, namespace: str) -> str:
    """Get logs from a job's pod."""
    try:
        result = subprocess.run(
            ["kubectl", "logs", "-n", namespace, "-l", f"job-name={job_name}"],
            capture_output=True,
            text=True,
        )
        return result.stdout
    except Exception:
        return ""


def delete_job(job_name: str, namespace: str) -> bool:
    """Delete a Kubernetes Job."""
    try:
        subprocess.run(
            ["kubectl", "delete", "job", job_name, "-n", namespace],
            capture_output=True,
            check=True,
        )
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Spawn parallel Kubernetes agent jobs")
    parser.add_argument("--tasks", nargs="+", help="List of task prompts")
    parser.add_argument("--tasks-file", type=Path, help="File with tasks (one per line)")
    parser.add_argument("--namespace", default="warp-agents", help="Kubernetes namespace")
    parser.add_argument("--image", default="warpdotdev/dev-base:latest", help="Container image")
    parser.add_argument("--secret-name", default="warp-api-key", help="Secret containing WARP_API_KEY")
    parser.add_argument("--pvc-name", help="PVC name for workspace")
    parser.add_argument("--memory-request", default="2Gi", help="Memory request")
    parser.add_argument("--memory-limit", default="4Gi", help="Memory limit")
    parser.add_argument("--cpu-request", default="1", help="CPU request")
    parser.add_argument("--cpu-limit", default="2", help="CPU limit")
    parser.add_argument("--share", default="team", choices=["team", "public", "private"], help="Session sharing")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout per job (seconds)")
    parser.add_argument("--wait", action="store_true", help="Wait for all jobs to complete")
    parser.add_argument("--dry-run", action="store_true", help="Print manifests without applying")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    add_log_format_arg(parser)

    args = parser.parse_args()
    _log_configure(
        format=getattr(args, "log_format", "text"),
        flush_each=getattr(args, "log_flush_each", False),
    )
    
    # Get tasks
    tasks = []
    if args.tasks:
        tasks = args.tasks
    elif args.tasks_file:
        tasks = [line.strip() for line in args.tasks_file.read_text().splitlines() if line.strip()]
    else:
        print("Error: Must provide --tasks or --tasks-file", file=sys.stderr)
        sys.exit(1)
    
    # Create namespace if it doesn't exist
    if not args.dry_run:
        subprocess.run(
            ["kubectl", "create", "namespace", args.namespace],
            capture_output=True,
        )
    
    # Create and apply jobs
    results: list[JobResult] = []

    print(f"Creating {len(tasks)} Kubernetes Jobs...", file=sys.stderr)
    log_event("spawn.start", backend="k8s", tasks=len(tasks), namespace=args.namespace)
    
    for i, task in enumerate(tasks):
        job_name = generate_job_name(task, i)
        
        manifest = create_job_manifest(
            task=task,
            job_name=job_name,
            namespace=args.namespace,
            image=args.image,
            secret_name=args.secret_name,
            pvc_name=args.pvc_name,
            memory_request=args.memory_request,
            memory_limit=args.memory_limit,
            cpu_request=args.cpu_request,
            cpu_limit=args.cpu_limit,
            share=args.share,
        )
        
        if args.dry_run:
            print("---")
            print(yaml.dump(manifest))
            results.append(JobResult(
                task_id=job_name,
                task=task,
                job_name=job_name,
                status="dry-run",
            ))
        else:
            success, output = apply_manifest(manifest)
            if success:
                print(f"✓ Created: {job_name} ({task[:50]}...)", file=sys.stderr)
                log_event("spawn.container.started", backend="k8s", task_id=job_name, namespace=args.namespace)
                results.append(JobResult(
                    task_id=job_name,
                    task=task,
                    job_name=job_name,
                    status="pending",
                ))
            else:
                print(f"✗ Failed to create: {job_name} - {output}", file=sys.stderr)
                log_event("spawn.container.started", backend="k8s", task_id=job_name, status="failed", error=output[:200])
                results.append(JobResult(
                    task_id=job_name,
                    task=task,
                    job_name=job_name,
                    status="failed",
                    error=output,
                ))
    
    # Wait for completion if requested
    if args.wait and not args.dry_run:
        print("\nWaiting for jobs to complete...", file=sys.stderr)
        
        for result in results:
            if result.status == "pending":
                status = wait_for_job(result.job_name, args.namespace, args.timeout)
                result.status = status
                
                if status == "completed":
                    print(f"✓ Completed: {result.job_name}", file=sys.stderr)
                    log_event("spawn.container.completed", backend="k8s", task_id=result.job_name, status="completed")
                elif status == "timeout":
                    print(f"⏱ Timeout: {result.job_name}", file=sys.stderr)
                    log_event("spawn.container.completed", backend="k8s", task_id=result.job_name, status="timeout")
                else:
                    print(f"✗ Failed: {result.job_name}", file=sys.stderr)
                    log_event("spawn.container.completed", backend="k8s", task_id=result.job_name, status="failed")
    
    # Output results
    if args.json:
        output = {
            "namespace": args.namespace,
            "total": len(results),
            "pending": sum(1 for r in results if r.status == "pending"),
            "running": sum(1 for r in results if r.status == "running"),
            "completed": sum(1 for r in results if r.status == "completed"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "results": [
                {
                    "task_id": r.task_id,
                    "task": r.task,
                    "job_name": r.job_name,
                    "status": r.status,
                    "error": r.error,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    elif not args.dry_run:
        print(f"\nSummary:", file=sys.stderr)
        print(f"  Namespace: {args.namespace}", file=sys.stderr)
        print(f"  Total: {len(results)}", file=sys.stderr)
        print(f"  Pending: {sum(1 for r in results if r.status == 'pending')}", file=sys.stderr)
        print(f"  Running: {sum(1 for r in results if r.status == 'running')}", file=sys.stderr)
        print(f"  Completed: {sum(1 for r in results if r.status == 'completed')}", file=sys.stderr)
        print(f"  Failed: {sum(1 for r in results if r.status == 'failed')}", file=sys.stderr)
        
        print("\nMonitor with:", file=sys.stderr)
        print(f"  kubectl get jobs -n {args.namespace} -l app=warp-agent -w", file=sys.stderr)


if __name__ == "__main__":
    main()
