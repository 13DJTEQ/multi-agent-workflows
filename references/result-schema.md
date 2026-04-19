# Result Schema Standardization

Should sub-agents emit results in a standard schema? This doc captures the tradeoffs, the recommended minimal schema, and how it affects aggregation.

## The Question

> Should we enforce a standard output schema for aggregation?

Two extremes:

- **Free-form:** Each agent writes whatever JSON makes sense. Aggregation must be bespoke per workflow.
- **Rigid schema:** Every agent emits the exact same envelope. Aggregation is trivial but agents have less room to express nuance.

The right answer is almost always a **minimal common envelope with a free-form payload**.

## Implications of Standardizing

### If you enforce a schema

Pros:
- `aggregate_results.py` works out of the box with every workflow.
- Cross-workflow tooling (dashboards, metrics, failure analysis) becomes possible.
- Easier to detect partial/malformed outputs.
- Lints catch schema drift early.

Cons:
- Sub-agents must be prompted to conform — one more source of prompt failure.
- Rigid schemas create friction for novel tasks (e.g., an agent producing a binary artifact vs. text).
- Schema evolution becomes a coordination tax across all workflows.
- Over-specification slows adoption.

### If you don't enforce a schema

Pros:
- Maximum flexibility per workflow.
- Lower prompt complexity — agents just emit whatever makes sense.

Cons:
- Every workflow re-invents aggregation. This is where most bugs live.
- No shared metrics across workflows.
- Failure analysis is per-workflow guesswork.
- Prompt drift between agents in the same workflow produces unmerging outputs.

## Recommended: Minimal Common Envelope

Adopt a small envelope with two required fields and a free-form payload:

```json
{
  "schema_version": "1",
  "status": "ok",
  "task_id": "analyze-auth",
  "data": { ... workflow-specific ... }
}
```

Required:
- `schema_version` (string): start at `"1"`. Bump on breaking changes.
- `status` (enum): `"ok"` | `"partial"` | `"failed"`.

Optional but recommended:
- `task_id` (string): matches the `task_id` used by spawn_docker.
- `error` (string): populated when `status != "ok"`.
- `data` (object|array|string): the actual result payload.
- `metrics` (object): `{duration_seconds, tokens_used, ...}` for cost/perf analysis.
- `provenance` (object): `{agent, model, commit, started_at, finished_at}`.

## JSON Schema (optional enforcement)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schema_version", "status"],
  "properties": {
    "schema_version": {"type": "string", "const": "1"},
    "status": {"enum": ["ok", "partial", "failed"]},
    "task_id": {"type": "string"},
    "error": {"type": "string"},
    "data": {},
    "metrics": {"type": "object"},
    "provenance": {"type": "object"}
  },
  "additionalProperties": true
}
```

Validate with:
```bash
python3 -c "
import json, jsonschema, sys
schema = json.load(open('references/result-schema.json'))
for path in sys.argv[1:]:
    jsonschema.validate(json.load(open(path)), schema)
    print(f'{path}: OK')
" outputs/*/result.json
```

## Aggregation implications

The envelope maps cleanly to the strategies in `aggregation-patterns.md`:

- **merge** — merges each `data` object, surfacing per-source `status` in metadata.
- **concat** — concatenates `data` arrays; skips any entry with `status == "failed"`.
- **vote** — requires `data` to be a scalar or a voteable field; uses `status == "ok"` entries only.
- **latest** — uses `provenance.finished_at` to pick the most recent.

`aggregate_results.py` today already tolerates free-form JSON. Adopting the envelope means:
- `--min-success` can use `status` directly instead of exit codes.
- Stats output can include a per-status breakdown.
- Malformed outputs fail loudly rather than silently corrupting the merge.

## Migration path

1. **Today:** Document the envelope. Don't enforce.
2. **Next:** Update example prompts (`examples/`) to request the envelope. Agents that follow prompts will start emitting it.
3. **Later:** Add optional schema validation to `aggregate_results.py` (flag-gated, `--validate-schema`).
4. **Eventually:** Make validation the default. Add a `--no-validate` escape hatch.

Do not skip steps 1–3. Rolling out schema enforcement without measuring impact creates exactly the rigidity problem we're trying to avoid.

## What NOT to standardize

- **Payload shape.** Let workflows define their own `data` structure.
- **File layout.** `result.json` is a convention, not a contract. Agents may emit `outputs/report.md` or whatever suits them.
- **Aggregation semantics.** Different workflows need different merge strategies.

## Non-envelope outputs

Some agents produce non-JSON artifacts (binaries, images, very large text). For these:

- Emit a `result.json` envelope that points to the artifact: `{"data": {"artifact_path": "output.bin"}}`.
- Keep the aggregator working with pointers instead of inline payloads.
