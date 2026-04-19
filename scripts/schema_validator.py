#!/usr/bin/env python3
"""
Validate agent result envelopes against references/result-schema.json.

Provides:
- Library API: `validate_envelope(obj) -> ValidationResult`
- CLI: `python3 scripts/schema_validator.py outputs/*/result.json`

Works with or without the `jsonschema` package installed:
- If `jsonschema` is available, full draft-2020-12 validation runs.
- Otherwise, a built-in minimal validator checks the envelope's required
  fields and enum values (sufficient for the v1 envelope; falls back gracefully).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_SCHEMA_PATH = _HERE.parent / "references" / "result-schema.json"

VALID_STATUS = {"ok", "partial", "failed"}
SCHEMA_VERSION_CONST = "1"


@dataclass
class ValidationResult:
    """Result of validating one envelope. `ok=True` means it passed."""

    ok: bool
    path: Optional[Path] = None
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def _load_schema() -> dict:
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema not found at {_SCHEMA_PATH}. " "This file ships with the skill; reinstall if missing."
        )
    return json.loads(_SCHEMA_PATH.read_text())


def _builtin_validate(obj: Any) -> list[str]:
    """Minimal validator used when the `jsonschema` package is unavailable.

    Checks the invariants v1 callers actually rely on:
    - obj is a dict
    - has required fields: schema_version, status
    - schema_version == "1"
    - status in {ok, partial, failed}
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return [f"envelope must be a JSON object, got {type(obj).__name__}"]
    if "schema_version" not in obj:
        errors.append("missing required field: schema_version")
    elif obj["schema_version"] != SCHEMA_VERSION_CONST:
        errors.append(f"schema_version must be '{SCHEMA_VERSION_CONST}', got {obj['schema_version']!r}")
    if "status" not in obj:
        errors.append("missing required field: status")
    elif obj["status"] not in VALID_STATUS:
        errors.append(f"status must be one of {sorted(VALID_STATUS)}, got {obj['status']!r}")
    # metrics / provenance must be dicts if present
    for field_name in ("metrics", "provenance"):
        if field_name in obj and not isinstance(obj[field_name], dict):
            errors.append(f"{field_name} must be an object if present")
    return errors


def _jsonschema_validate(obj: Any, schema: dict) -> list[str]:
    import jsonschema  # type: ignore

    validator = jsonschema.Draft202012Validator(schema)
    return [e.message for e in sorted(validator.iter_errors(obj), key=lambda e: e.path)]


def validate_envelope(obj: Any, schema: Optional[dict] = None) -> ValidationResult:
    """Validate a single envelope object. Returns ValidationResult(ok, errors).

    Uses `jsonschema` if importable; otherwise falls back to built-in checks.
    """
    if schema is None:
        schema = _load_schema()
    try:
        errors = _jsonschema_validate(obj, schema)
    except ImportError:
        errors = _builtin_validate(obj)
    return ValidationResult(ok=not errors, errors=errors)


def validate_file(path: Path, schema: Optional[dict] = None) -> ValidationResult:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return ValidationResult(ok=False, path=path, errors=[f"failed to load {path}: {e}"])
    result = validate_envelope(data, schema=schema)
    result.path = path
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate agent result envelopes against the v1 schema.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s outputs/**/result.json
  %(prog)s path/to/one.json --quiet
""",
    )
    parser.add_argument("files", nargs="+", type=Path, help="Envelope JSON files to validate")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-file OK lines; only print failures")
    args = parser.parse_args()

    schema = _load_schema()
    failed = 0
    for p in args.files:
        r = validate_file(p, schema=schema)
        if r.ok:
            if not args.quiet:
                print(f"\u2713 {p}")
        else:
            failed += 1
            print(f"\u2717 {p}")
            for e in r.errors:
                print(f"  - {e}")
    if failed:
        print(f"\n{failed} of {len(args.files)} files failed validation.", file=sys.stderr)
        return 1
    print(f"\n{len(args.files)} of {len(args.files)} files valid.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
