"""Structured log emitter for multi-agent-workflows scripts.

Emits either human-readable or newline-delimited JSON lines to stderr so
operators can pipe through `jq` or ship to an observability backend.

Design:
- Module-private (`_log`) because the schema is not yet a public contract.
- Two formats:
    * text (default): `[timestamp] event key=value key=value ...` (compact, greppable)
    * json: one NDJSON line per event with fixed top-level keys
      (`ts`, `event`) + user-supplied fields flattened in.
- `configure(format, stream)` is process-global; each script calls it once
  during arg parsing and all subsequent `log_event` calls honor it.
- Events are intentionally free-form at the field level; the stable contract
  is only the top-level `ts` + `event` plus per-call required fields that
  individual call sites document.

Event taxonomy (see plan P1-B):
    spawn.start, spawn.container.started, spawn.container.completed,
    spawn.circuit_breaker.tripped,
    phase.wait.start, phase.wait.done,
    aggregate.start, aggregate.done
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, TextIO

_FORMAT = "text"  # "text" | "json"
_STREAM: TextIO = sys.stderr


def configure(format: str = "text", stream: TextIO | None = None) -> None:
    """Configure the process-wide log emitter.

    Args:
        format: "text" (default, human-readable) or "json" (NDJSON).
        stream: File-like object. Defaults to sys.stderr.
    """
    global _FORMAT, _STREAM
    if format not in {"text", "json"}:
        raise ValueError(f"log format must be 'text' or 'json', got {format!r}")
    _FORMAT = format
    _STREAM = stream if stream is not None else sys.stderr


def log_event(event: str, **fields: Any) -> None:
    """Emit a single structured event.

    The `event` name should be dot-separated (e.g. ``spawn.container.started``).
    Field values must be JSON-serializable; non-serializable values are
    stringified with ``str()`` as a defensive fallback.
    """
    ts = time.time()
    if _FORMAT == "json":
        record: dict[str, Any] = {"ts": ts, "event": event}
        record.update(fields)
        try:
            line = json.dumps(record, default=str)
        except (TypeError, ValueError):
            line = json.dumps({"ts": ts, "event": event, "error": "serialize failed"})
        _STREAM.write(line + "\n")
    else:
        # Text format: [iso_ts] event key=value key=value
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        parts = [f"[{iso}]", event]
        for k, v in fields.items():
            parts.append(f"{k}={v!r}" if isinstance(v, str) and " " in v else f"{k}={v}")
        _STREAM.write(" ".join(parts) + "\n")
    _STREAM.flush()


def add_log_format_arg(parser) -> None:
    """Convenience: add --log-format to an argparse parser."""
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Log output format (default: %(default)s). Use 'json' for NDJSON suitable for jq/OTEL ingestion.",
    )
