# Isolate Service

A lightweight V8/WASM sandbox execution service you run on your own hardware. Think of it as your own personal [Cloudflare Dynamic Workers](https://blog.cloudflare.com/cloudflare-workers-dynamic-dispatcher/) — but local, self-hosted, and built for home infra monitoring.

---

## What It Does

Runs untrusted JavaScript in sandboxed V8 isolates via a simple HTTP API. Each execution gets its own isolated context — no filesystem access, no network, no `require()`, no `process`. Just compute, with only the APIs you explicitly allow.

## Quick Start

### Requirements

- **Node.js** v18+ (uses `vm` module for V8 isolates)
- **Deno** 2.7+ (optional — for heavier Web Worker isolates with TypeScript)

### Install & Run

```bash
git clone https://github.com/scotthawk-maker/isolate-service.git
cd isolate-service
npm install
node src/server.js
```

The service starts on `http://127.0.0.1:5900` by default.

### Verify It's Running

```bash
curl http://127.0.0.1:5900/health
```

---

## API Reference

### POST /execute

Run JavaScript in a V8 isolate sandbox.

```bash
curl -s -X POST http://127.0.0.1:5900/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "return env.prices.reduce((a,b) => a+b, 0) / env.prices.length",
    "preset": "compute",
    "env": { "prices": [83.50, 84.20, 82.10] },
    "timeout": 5000
  }'
```

**Parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | JavaScript to execute. Use `return` to return a value. |
| `preset` | string | API exposure level: `minimal`, `compute`, `data`, `agent` |
| `env` | object | Data injected into the sandbox as `env` (and `input`) |
| `timeout` | number | Max execution time in ms (default: 5000, max: 30000) |

### POST /validate

Syntax-check code without executing it.

### POST /wasm/compile

Compile a WASM module from base64-encoded bytes or a file path.

### POST /wasm/run

Execute a previously compiled WASM module.

### POST /deno

Execute code in a Deno Web Worker (heavier isolation with TypeScript support). Deno workers run with **zero permissions** by default.

### GET /health

Service health check with engine stats.

### GET /stats

Detailed engine statistics.

---

## Context Presets

Presets control what APIs the sandbox can access. Principle of least privilege — only expose what the code actually needs.

| Preset | Includes | Use for |
|--------|----------|---------|
| `minimal` | Nothing — pure JS language features | Math formulas, simple transforms |
| `compute` | `Math`, `JSON`, `Array`, `Object`, `String`, `Number`, `Date`, `Map`, `Set`, `Promise`, `RegExp`, `Error` | Filtering, grouping, aggregating, calculations |
| `data` | Compute + `Buffer`, `TextEncoder`, `TextDecoder`, `btoa`, `atob`, `crypto.subtle` | Encoding, hashing, binary data |
| `agent` | Data + controlled `fetch` and `console` (per-execution only) | AI agent outputs needing controlled network access |

---

## Security

### Sandbox Isolation

The V8 `vm.createContext()` API creates a fresh global object. Code running inside can only access what you explicitly pass in — there is no `require`, no `process`, no `fs`.

### Prototype Pollution Protection

Built-in prototypes are frozen at context creation:

```javascript
Object.freeze(Object.prototype);
Object.freeze(Array.prototype);
Object.freeze(Function.prototype);
Object.freeze(String.prototype);
Object.freeze(Number.prototype);
Object.freeze(Boolean.prototype);
```

### What Isolate Security Is NOT

V8 `vm.createContext()` is **not** a security boundary against determined attackers. For truly untrusted code — user-submitted scripts, public-facing APIs — use Docker containers or hardware VMs. Isolates are defense-in-depth against accidental harm and AI-generated code.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│             HTTP API (port 5900)             │
│  /execute  /validate  /deno  /wasm/*  /stats │
├─────────────────────────────────────────────┤
│           Isolate Engine (V8 vm)            │
│  ┌───────────┐  ┌───────────┐               │
│  │ Context 1 │  │ Context 2 │  ... (50 max) │
│  │ (frozen   │  │ (frozen   │               │
│  │  protos)  │  │  protos)  │               │
│  └───────────┘  └───────────┘               │
├─────────────────────────────────────────────┤
│          WASM Engine (compile cache)        │
├─────────────────────────────────────────────┤
│       Worker Pool (Deno, 0 permissions)     │
└─────────────────────────────────────────────┘
```

### Code Structure

```
isolate-service/
├── src/
│   └── server.js          # HTTP server, routing, request handling
├── lib/
│   ├── isolate.js          # V8 sandbox engine (core execution, presets, frozen prototypes)
│   ├── wasm-isolate.js     # WASM compile/cache/execute with WASI stubs
│   └── worker-pool.js      # Deno Web Worker pool (0 permissions by default)
├── config/
│   └── default.js          # All configuration (timeouts, limits, auth, presets)
├── routes/
│   └── auth.js             # Bearer token auth + rate limiting middleware
├── isolate-service.service  # Example systemd unit file
├── package.json
├── LICENSE
└── README.md
```

---

## Configuration

All configuration is in `config/default.js`, overridable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ISOLATE_PORT` | 5900 | Server port |
| `ISOLATE_HOST` | 127.0.0.1 | Server bind address |
| `ISOLATE_AUTH_TOKEN` | null | Bearer token for auth (null = open) |
| `ISOLATE_LOG_LEVEL` | info | Logging level |
| `DENO_PATH` | `deno` | Path to Deno binary |

---

## License

MIT — see [LICENSE](LICENSE).