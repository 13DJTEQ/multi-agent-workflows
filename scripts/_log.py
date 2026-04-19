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

import atexit
import json
import sys
import time
from typing import Any, TextIO

_FORMAT = "text"  # "text" | "json"
_STREAM: TextIO = sys.stderr

# P1-D: buffered flush. Default thresholds chosen so an interactive
# operator sees output roughly once a second but high-throughput spawns
# don't pay a flush on every event.
_FLUSH_EACH = False  # if True, flush after every event (legacy behavior)
_FLUSH_INTERVAL_EVENTS = 50
_FLUSH_INTERVAL_SECONDS = 1.0
_events_since_flush = 0
_last_flush_ts = 0.0
_atexit_registered = False


def _flush() -> None:
    global _events_since_flush, _last_flush_ts
    try:
        _STREAM.flush()
    except (OSError, ValueError):
        # Stream may already be closed at interpreter shutdown; swallow.
        pass
    _events_since_flush = 0
    _last_flush_ts = time.time()


def _ensure_atexit() -> None:
    """Register a one-time atexit flush so buffered events aren't dropped."""
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_flush)
        _atexit_registered = True


def configure(
    format: str = "text",
    stream: TextIO | None = None,
    flush_each: bool = False,
    flush_interval_events: int = _FLUSH_INTERVAL_EVENTS,
    flush_interval_seconds: float = _FLUSH_INTERVAL_SECONDS,
) -> None:
    """Configure the process-wide log emitter.

    Args:
        format: "text" (default, human-readable) or "json" (NDJSON).
        stream: File-like object. Defaults to sys.stderr.
        flush_each: If True, flush on every event (legacy behavior).
        flush_interval_events: Flush when this many events are buffered.
        flush_interval_seconds: Flush when this many seconds have elapsed.
    """
    global _FORMAT, _STREAM, _FLUSH_EACH, _FLUSH_INTERVAL_EVENTS, _FLUSH_INTERVAL_SECONDS
    global _events_since_flush, _last_flush_ts
    if format not in {"text", "json"}:
        raise ValueError(f"log format must be 'text' or 'json', got {format!r}")
    _FORMAT = format
    _STREAM = stream if stream is not None else sys.stderr
    _FLUSH_EACH = bool(flush_each)
    _FLUSH_INTERVAL_EVENTS = max(1, int(flush_interval_events))
    _FLUSH_INTERVAL_SECONDS = max(0.0, float(flush_interval_seconds))
    _events_since_flush = 0
    _last_flush_ts = time.time()
    _ensure_atexit()


def log_event(event: str, **fields: Any) -> None:
    """Emit a single structured event.

    The `event` name should be dot-separated (e.g. ``spawn.container.started``).
    Field values must be JSON-serializable; non-serializable values are
    stringified with ``str()`` as a defensive fallback.

    Flush policy (P1-D): buffered by default. Flush fires when any of these
    trip:
      - `flush_each=True` was passed to configure()
      - events-since-last-flush >= flush_interval_events
      - seconds-since-last-flush >= flush_interval_seconds
      - atexit (registered once at configure time)
    """
    global _events_since_flush
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
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        parts = [f"[{iso}]", event]
        for k, v in fields.items():
            parts.append(f"{k}={v!r}" if isinstance(v, str) and " " in v else f"{k}={v}")
        _STREAM.write(" ".join(parts) + "\n")

    _events_since_flush += 1
    if _FLUSH_EACH:
        _flush()
        return
    if _events_since_flush >= _FLUSH_INTERVAL_EVENTS:
        _flush()
        return
    if _FLUSH_INTERVAL_SECONDS and (ts - _last_flush_ts) >= _FLUSH_INTERVAL_SECONDS:
        _flush()


def flush() -> None:
    """Public flush hook for callers that want to force immediate drain."""
    _flush()


def add_log_format_arg(parser) -> None:
    """Convenience: add --log-format and --log-flush-each to an argparse parser."""
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Log output format (default: %(default)s). Use 'json' for NDJSON suitable for jq/OTEL ingestion.",
    )
    parser.add_argument(
        "--log-flush-each",
        action="store_true",
        help="Flush after every event (slower, for live tailing). Default: buffered with 1s / 50-event thresholds and atexit safety flush.",
    )
