# AI Agent Instructions

This file provides context for AI coding agents (Claude, GPT, Copilot, etc.) working with this codebase.

## Project Overview

Isolate Service is a local V8/WASM sandbox execution service. It runs JavaScript in sandboxed V8 contexts via an HTTP API, inspired by Cloudflare Dynamic Workers. It also powers monitoring agents that use isolate-based threshold analysis.

**Do NOT add** filesystem access, network access, or `require()` to the sandbox. The entire point is that code running inside isolates cannot reach the host. If a feature needs host access, it belongs in the Python agent layer or the server itself — never in the V8 context.

## Architecture

```
HTTP Request → server.js (routing) → isolate.js (V8 sandbox) → response
                                 → wasm-isolate.js (WASM compile/execute)
                                 → worker-pool.js (Deno workers)
```

**Data flow for agents:**
```
Cron → Python script (fetch data from APIs) → curl POST to /execute (sandbox analysis) → alert/handle
```

The Python script fetches data OUTSIDE the isolate (network access), the isolate analyzes data INSIDE (no network). This separation is critical — don't blur it.

## File Responsibilities

| File | What it does | Don't use it for |
|------|-------------|-----------------|
| `src/server.js` | HTTP routing, request parsing, response formatting | Business logic, execution |
| `lib/isolate.js` | V8 context creation, preset APIs, code execution, frozen prototypes | HTTP handling, WASM |
| `lib/wasm-isolate.js` | WASM compile, cache, execute, WASI stubs | V8 sandboxing, JS execution |
| `lib/worker-pool.js` | Deno Web Worker pool for heavier isolated tasks | Quick <30s tasks (use isolate.js) |
| `config/default.js` | All configuration constants | Runtime state |
| `routes/auth.js` | Bearer token auth + rate limiting | Anything else |
| `agents/*.py` | Data fetching + isolate analysis (monitoring) | Business logic in Python — keep it in JS isolate |

## Key Conventions

1. **All isolates get frozen prototypes.** The freeze happens in `lib/isolate.js` after `vm.createContext()`. If you add new built-in types to presets, freeze their prototypes too.
2. **Presets are cumulative**: minimal < compute < data < agent. When adding APIs, add them to the most restrictive preset that needs them.
3. **Agent JavaScript goes in the `code` field of the isolate payload.** Python agents only fetch data and deliver alerts — all analysis logic runs in the sandbox.
4. **Use IIFE wrappers** for isolate code: `(function(){ ... })()`. Bare `return` at top level is a SyntaxError in V8 contexts.
5. **Write payloads to temp file, curl with `@file`**. Shell escaping of JSON in `-d` flag is unreliable. All agents follow this pattern.
6. **Exit codes**: 0 = OK (silence), 1 = WARNING, 2 = CRITICAL. Cron prompts use these to decide whether to relay alerts.

## Testing Changes

```bash
# Test the API directly
curl -s -X POST http://127.0.0.1:5900/execute \
  -H "Content-Type: application/json" \
  -d '{"code": "return 1+1", "preset": "compute"}'

# Check health
curl http://127.0.0.1:5900/health

# Run an agent manually
python3 agents/lp-sentinel.py; echo "Exit: $?"
```

## Common Tasks

### Adding a new API to the compute preset
Edit `CONTEXT_PRESETS` in `lib/isolate.js`. Add to the appropriate preset level. If it's a built-in type, also add a `Object.freeze(X.prototype)` line in the freeze block.

### Adding a new monitoring agent
1. Create `agents/your-agent.py`
2. Follow the pattern: fetch data → build isolate payload with JS threshold logic → write to temp file → curl POST → parse result → exit 0/1/2
3. Add agent description to README.md under "Monitoring Agents"

### Adding a new API endpoint
1. Add the route handler in `src/server.js` (follow the existing pattern)
2. Add the endpoint to the 404 response's `endpoints` list
3. Document in README.md API Reference section

## Security Model

- V8 `vm.createContext()` is NOT a security boundary against determined attackers
- It IS defense-in-depth against accidental harm and AI-generated code
- For truly untrusted code, recommend Docker/Sandbox containers
- Never add `require`, `process`, `fs`, or `child_process` to any preset