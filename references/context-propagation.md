# Context Propagation

How much of the coordinator's context should each sub-agent receive? Pick the narrowest strategy that still lets the sub-agent succeed — context is the dominant cost driver and the biggest source of hallucinations.

## Strategies

### 1. Rules-only (minimum viable)

Pass only the `.warp/rules.md` (or equivalent) into the agent's workspace. No conversation history, no parent prompt.

**Best for:** Highly decomposable tasks with self-contained prompts (e.g., "Run pytest on tests/auth/").

**Implementation:**
```bash
docker run \
  -v "$HOME/.warp:/root/.warp:ro" \
  -v "$PWD:/workspace" \
  -e WARP_API_KEY="$WARP_API_KEY" \
  warpdotdev/dev-base:latest \
  oz agent run --prompt "$TASK"
```

**Cost:** ~1–5 KB per agent. **Risk:** Agent may miss project-specific conventions not captured in rules.

### 2. Explicit injection (recommended default)

Compose a task-specific context bundle (files, URLs, snippets) and inline it into the prompt or mount as `/context/`.

**Best for:** Most production workflows — gives you control without leaking unrelated history.

**Implementation:**
```bash
# Prepare per-task context
mkdir -p /tmp/ctx/task-1
cp docs/auth-spec.md /tmp/ctx/task-1/
echo "Related files: src/auth/login.py" > /tmp/ctx/task-1/hints.txt

docker run \
  -v "/tmp/ctx/task-1:/context:ro" \
  -v "$PWD:/workspace" \
  -e CONTEXT_DIR=/context \
  warpdotdev/dev-base:latest \
  oz agent run --prompt "$TASK. Read /context/ for domain-specific notes."
```

**Cost:** Proportional to bundle size (you choose). **Risk:** Forgetting a critical file leads to rework.

### 3. Conversation summary (medium fidelity)

Serialize the coordinator's recent conversation summary (last N turns) and pass it as a prompt prefix or a file.

**Best for:** Multi-phase workflows where phase N needs to know what phase N-1 decided.

**Implementation:**
```bash
# Coordinator writes summary per phase
cat > /tmp/summary-phase1.md <<EOF
## Phase 1 Summary
- Analyzed auth module, found 3 hardcoded secrets
- API layer uses deprecated v1 endpoints in 5 files
- Decision: remove all v1 references before adding v2
EOF

python3 scripts/spawn_docker.py \
  --tasks "Implement phase 1 decisions. Read /summary/ for context." \
  --docker-args "-v /tmp:/summary:ro" \
  --env "SUMMARY_FILE=/summary/summary-phase1.md"
```

**Cost:** ~5–50 KB per agent. **Risk:** Summary accuracy — stale or misleading summaries silently poison downstream decisions.

### 4. Full conversation (avoid)

Dump the entire parent conversation into each agent's prompt.

**When it's the right call:** Almost never. Only when the task explicitly requires multi-turn context (e.g., "continue the refactor the user and I discussed").

**Why avoid:**
- Context windows fill quickly; sub-agents burn tokens parsing irrelevant history.
- Privacy: unrelated data leaks into every agent.
- Confuses the agent — it may re-answer earlier questions.

**Implementation (if you must):**
```bash
# Export conversation, then inline
oz conversation export --format markdown > /tmp/convo.md
docker run \
  -v /tmp/convo.md:/context/convo.md:ro \
  ... \
  oz agent run --prompt "Full convo context at /context/convo.md. TASK: $TASK"
```

## Decision Matrix

| Strategy | Tokens | Accuracy | Use when |
|----------|--------|----------|----------|
| Rules-only | ~1–5 KB | Low–Medium | Self-contained tasks |
| Explicit injection | Variable | High | Default for production |
| Conversation summary | ~5–50 KB | Medium–High | Multi-phase workflows |
| Full conversation | 100+ KB | High | Rare — continuation tasks only |

## Propagation Mechanics

Regardless of strategy, context reaches sub-agents through four channels:

1. **Prompt text** — inline in the `--prompt` argument. Best for small context (<2 KB).
2. **Mounted files** — `-v /ctx:/ctx:ro`. Best for medium context (<1 MB).
3. **Environment variables** — `-e CTX_URL=...`. Best for pointers/references.
4. **Shared volume** — writable `/shared` for cross-agent communication.

## Anti-patterns

- **Dumping the whole repo** into context. Agents should read files on-demand via their workspace mount.
- **Reusing the same context blob for every task.** Different sub-tasks need different slices.
- **Passing secrets in context.** Use `credential_helper.py` and environment variables; never inline API keys in prompts.
- **Propagating context from untrusted sources** (e.g., user-supplied URLs) into the prompt without sanitization.

## Recommended default

For new workflows: start with **rules-only + explicit injection**. Measure whether agents succeed. Only escalate to summary/full if you observe failures that stem from missing context.
