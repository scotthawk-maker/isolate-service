# Contributing with AI Agents

This project is AI-friendly. Here are ready-to-use prompts for common tasks. Copy them into your AI agent (Claude, GPT, Copilot, Hermes, etc.) along with the relevant source files.

---

## Prompt: Add a New Monitoring Agent

```
You are working on the isolate-service project — a V8/WASM sandbox execution service that also powers monitoring agents.

Read AGENTS.md for architecture and conventions.

Create a new monitoring agent at agents/<name>.py following this exact pattern:

1. DATA COLLECTION — Python functions that fetch data from APIs/system. No analysis logic here.
2. ISOLATE ANALYSIS — Build a JSON payload with:
   - "code": IIFE-wrapped JavaScript that takes env.data and returns {alerts, status, summary}
   - "preset": "compute" (default) or "minimal" if no Math/JSON needed
   - "env": the fetched data
3. Write payload to /tmp/<name>-payload.json, then curl -d @file (avoids shell escaping)
4. Parse isolate result, build alert message, exit 0/1/2 based on status

The agent should:
- Check for a PAUSE_FILE at /tmp/<name>-paused at the top of main()
- Only output when something is wrong (silence = OK)
- Use exit codes: 0=OK, 1=WARNING, 2=CRITICAL
- Include a summary dict in the isolate return value

Also update README.md to document the new agent.
```

---

## Prompt: Add a New Context Preset

```
You are working on isolate-service — a V8 sandbox execution service.

Read lib/isolate.js, particularly CONTEXT_PRESETS and the freeze block.

Add a new preset called "<name>" with these APIs: <list APIs>.

Rules:
1. Presets are cumulative: minimal < compute < data < agent. Place the new preset at the right level.
2. If the preset adds a built-in type, add Object.freeze(X.prototype) in the freeze block after vm.createContext().
3. Update the preset description.
4. Add the preset to /stats endpoint output in src/server.js (it already loops PRESETS keys).
5. Document in README.md under "Context Presets".
6. Test by posting to /execute with the new preset name.

Do NOT add require, process, fs, child_process, or network access to any preset.
```

---

## Prompt: Fix a Security Issue

```
You are working on isolate-service — a V8 sandbox execution service.

Read lib/isolate.js, which handles V8 context creation and code execution.

The security model is:
- V8 vm.createContext() creates isolated JavaScript contexts
- Built-in prototypes are frozen to prevent cross-execution pollution
- Code cannot access require, process, fs, or network
- Timeouts kill runaway code (default 5s, max 30s)

Reported issue: <describe the issue>

Fix it while maintaining these invariants:
1. Isolate code still has NO access to host resources
2. Frozen prototypes remain in place
3. The timeout mechanism still works
4. Existing API compatibility is preserved
5. Add a test case in the abuse test format (attack vector + expected result)

Document any new security considerations in README.md under "Security".
```

---

## Prompt: Add a New API Endpoint

```
You are working on isolate-service — a sandbox execution HTTP service on port 5900.

Read:
- src/server.js (routing and request handling)
- AGENTS.md (architecture and conventions)

Add a new endpoint: <describe endpoint>

Follow these patterns from server.js:
- Use sendJSON() for responses
- Use readBody() + parseJSON() for request parsing
- Auth check is already applied globally (skip for GET /health only)
- Add the endpoint to the 404 response's endpoints list
- Add elapsed time in _meta field

Document in README.md under "API Reference".
```

---

## Prompt: Understand the Codebase

```
Read the following files from the isolate-service project and explain how it works:

1. AGENTS.md — architecture, conventions, file responsibilities
2. src/server.js — HTTP API server
3. lib/isolate.js — V8 sandbox engine (the core)
4. lib/wasm-isolate.js — WASM execution
5. lib/worker-pool.js — Deno worker pool
6. config/default.js — configuration
7. agents/lp-sentinel.py — example monitoring agent

Explain:
- How a request flows from HTTP to V8 sandbox execution and back
- What the frozen prototype fix does and why
- How the preset system controls API exposure
- How monitoring agents separate fetching (Python) from analysis (JS sandbox)
- What security guarantees the sandbox provides and its limits
```