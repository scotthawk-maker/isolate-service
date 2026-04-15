# Isolate Service

A lightweight V8/WASM sandbox execution service you run on your own hardware. Think of it as your own personal [Cloudflare Dynamic Workers](https://blog.cloudflare.com/cloudflare-workers-dynamic-dispatcher/) — but local, self-hosted, and built for home infra monitoring.

---

## What It Does

Runs untrusted JavaScript in sandboxed V8 isolates via a simple HTTP API. Each execution gets its own isolated context — no filesystem access, no network, no `require()`, no `process`. Just compute, with only the APIs you explicitly allow.

**Real-world use: monitoring agents.** This service powers five autonomous monitoring agents that watch my home crypto infrastructure 24/7 — Monero mining, Solana LP positions, Docker containers, systemd services — all doing threshold analysis inside 1ms sandbox executions.

## Why This Exists

I run a small crypto stack at home (Monero mining, Solana DeFi, Hummingbot DEX gateway, a handful of Docker containers). I needed monitoring that was:

- **Fast enough to run every 5 minutes** without wasting resources
- **Secure enough to run AI-generated code** without risking the host
- **Light enough to not need Docker/VMs** for if-statements

Spinning up Docker containers for threshold checks is overkill. This service handles the analysis in ~1ms per execution, using V8's `vm.createContext()` for isolation instead of process-level sandboxing.

## How It Compares

| | Isolate | Docker Container | VM |
|---|---|---|---|
| Startup time | ~4ms | ~500ms | ~30s |
| Memory per execution | ~8MB | ~50MB | ~512MB |
| Filesystem access | No | Yes | Yes |
| Network access | No | Yes | Yes |
| Concurrent executions | Millions | Hundreds | Dozens |
| Cost per execution | ~$0 | ~$0.01 | ~$0.10 |

Isolates aren't a replacement for containers — they're for when you need sandboxed compute, not a full environment.

---

## Quick Start

### Requirements

- **Node.js** v18+ (uses `vm` module for V8 isolates)
- **Deno** 2.7+ (optional — for heavier Web Worker isolates with TypeScript)
- **wabt** (optional — for compiling WAT text to WASM binaries)

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

```json
{
  "status": "ok",
  "uptime": 42,
  "version": "0.1.0",
  "engine": { "active": 0, "totalExecutions": 0 }
}
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

**Response:**

```json
{
  "success": true,
  "result": 83.26666666666667,
  "logs": [],
  "executionId": "a1b2c3d4-...",
  "stats": {
    "elapsed": 0.87,
    "timeout": 5000,
    "preset": "compute",
    "logCount": 0
  }
}
```

### POST /validate

Syntax-check code without executing it.

```bash
curl -s -X POST http://127.0.0.1:5900/validate \
  -H "Content-Type: application/json" \
  -d '{ "code": "return 1 + " }'
```

Returns `{ "valid": false, "error": "..." }` or `{ "valid": true }`.

### POST /wasm/compile

Compile a WASM module from base64-encoded bytes or a file path.

```bash
# From a file
curl -s -X POST http://127.0.0.1:5900/wasm/compile \
  -H "Content-Type: application/json" \
  -d '{ "file": "/path/to/module.wasm", "id": "my-module" }'
```

Returns: `moduleId`, byte size, compile time, exports, and imports.

### POST /wasm/run

Execute a previously compiled WASM module.

```bash
curl -s -X POST http://127.0.0.1:5900/wasm/run \
  -H "Content-Type: application/json" \
  -d '{ "moduleId": "my-module", "entry": "add", "args": [42, 58] }'
```

### POST /deno

Execute code in a Deno Web Worker (heavier isolation with TypeScript support).

```bash
curl -s -X POST http://127.0.0.1:5900/deno \
  -H "Content-Type: application/json" \
  -d '{ "code": "return data.map(x => x * 2)", "data": [1, 2, 3] }'
```

Deno workers run with **zero permissions** by default (no filesystem, no network).

### GET /health

Service health check with engine stats.

### GET /stats

Detailed engine statistics — execution counts, success rates, average elapsed times, WASM cache, worker pool status.

---

## Context Presets

Presets control what APIs the sandbox can access. Principle of least privilege — only expose what the code actually needs.

### `minimal`

Pure compute. No APIs at all. Just JavaScript language features: variables, loops, conditionals.

Use for: mathematical formulas, simple transformations where you control both the code and data.

### `compute` (default)

Standard data operations. Includes: `Math`, `JSON`, `Array`, `Object`, `String`, `Number`, `Date`, `Map`, `Set`, `Promise`, `RegExp`, `Error`.

Use for: filtering, grouping, aggregating data, string manipulation, calculations.

### `data`

Compute + encoding and hashing. Adds: `Buffer`, `TextEncoder`, `TextDecoder`, `btoa`, `atob`, `crypto.subtle`.

Use for: data encoding, hashing, processing binary data.

### `agent`

Data + controlled HTTP fetch and logging. Fetch/console must be explicitly provided per-execution (cannot be injected globally).

Use for: AI agent outputs that need controlled network access.

---

## Monitoring Agents

The `agents/` directory contains five monitoring agents that demonstrate real-world usage of the isolate service. Each one follows the same pattern:

```
Cron (every 5-15 min) → Python script fetches data → isolate /execute → threshold analysis → alert if needed
```

The Python script is the **eyes** (curls APIs, reads system state). The isolate is the **brain** (runs threshold logic in a sandbox). This separation matters — the isolate can never accidentally hit your network or read your filesystem, regardless of what analysis code it runs.

### LP Sentinel

**File:** `agents/lp-sentinel.py` | **Interval:** 5 min

Monitors an Orca SOL/USDC concentrated liquidity position on Solana. Checks:
- Is the price still within the LP range?
- Is the wallet SOL buffer sufficient for rebalancing?
- Has position value dropped unexpectedly?
- Are supporting services still running?

**Sample alert:**
```
LP SENTINEL ALERT — WARNING
========================================
  [WARNING] LP OUT OF RANGE (ABOVE) — price $83.79 vs range $82.75-$83.59
  [INFO] Price near upper range edge (0.045% away)
========================================
  SOL: $83.79 | Range: $82.75-$83.59
  In Range: false | Value: $39.93
  SOL Buffer: 0.3456 SOL
```

### Infra Watchdog

**File:** `agents/infra-watchdog.py` | **Interval:** 15 min

Monitors all systemd services and key Docker containers. Checks:
- 5 systemd services active (xmrig, p2pool, wallet-rpc, isolate-service, webui)
- Key Docker containers running (gateway, hummingbot-api, postgres, broker)
- System load average
- Disk space usage (/ and /data)
- Remote Monero node reachability

### Hashrate Monitor

**File:** `agents/hashrate-monitor.py` | **Interval:** 15 min

Monitors XMRig Monero mining performance. Checks:
- Current hashrate vs expected baseline (~10.5 KH/s with MSR mod on Ryzen 9 7945HX)
- MSR register modifications still active (5-10% performance impact)
- P2Pool connectivity (shares are being submitted)
- Hashrate stability (10s vs 60s average divergence)

### Docker Health

**File:** `agents/docker-health.py` | **Interval:** 15 min

Monitors Docker container health at the container level. Checks:
- Container up/down status
- Health check results (healthy/unhealthy)
- Restart loops (>5 restarts = critical)
- Resource pressure (CPU >200%, memory >80%)
- Recently restarted containers

### Honcho Vault

**File:** `agents/honcho-vault.py` | **Interval:** 30 min

Not an alert agent — snapshots full system state into persistent memory (Honcho) every 30 minutes. This gives future AI sessions instant context without re-fetching everything. No Telegram delivery; saves locally.

---

## Security

### How the Sandbox Works

The V8 `vm.createContext()` API creates a new JavaScript context with a fresh global object. Code running inside can only access what you explicitly pass in — there is no `require`, no `process`, no `fs`, no `require('child_process')`.

### Prototype Pollution Fix

V8 shares `Object.prototype` across contexts by default. Code like:

```javascript
Object.prototype.evil = true
```

would leak into subsequent isolate executions. **This is now fixed.** The service freezes all built-in prototypes at context creation:

```javascript
Object.freeze(Object.prototype);
Object.freeze(Array.prototype);
Object.freeze(Function.prototype);
Object.freeze(String.prototype);
Object.freeze(Number.prototype);
Object.freeze(Boolean.prototype);
```

With frozen prototypes, pollution writes silently fail. No leak between executions.

### Abuse Test Results

15 attack vectors tested, all blocked:

| Attack | Result |
|--------|--------|
| `require('fs')` | SECURITY blocked |
| `require('child_process')` | SECURITY blocked |
| `process.env` | SECURITY blocked |
| `this.process` | SECURITY blocked |
| `eval('require')` | SECURITY blocked |
| Constructor chain escape | SECURITY blocked |
| `__proto__` pollution | SILENTLY REJECTED (frozen prototypes) |
| Infinite loop | TIMEOUT killed (5s default) |
| CPU bomb (nested loops) | TIMEOUT killed |
| Memory bomb (huge arrays) | TIMEOUT killed (heap hits limit) |
| Stack overflow (recursion) | RUNTIME killed |
| `async/await` bypass | Blocked (no microtask queue leak) |
| `fetch` in `minimal` preset | Blocked (API not available) |
| ES module `import` | SECURITY blocked |
| Global object access via `this` | SECURITY blocked |

### What Isolate Security Is NOT

V8 `vm.createContext()` is **not** a security boundary against determined attackers. V8 escape bugs exist (though they're rare and patched quickly). For truly untrusted code — user-submitted scripts, public-facing APIs — use Docker containers or hardware VMs instead. Isolates are for defense-in-depth against accidental harm and AI-generated code, not adversarial exploits.

---

## Configuration

All configuration is in `config/default.js`:

```javascript
module.exports = {
  server: {
    port: process.env.ISOLATE_PORT || 5900,
    host: process.env.ISOLATE_HOST || '127.0.0.1',
  },
  engine: {
    defaultTimeout: 5000,     // 5s default execution timeout
    maxTimeout: 30000,        // 30s hard cap
    maxMemoryMB: 128,         // V8 heap limit per context
    maxConcurrent: 50,        // Max simultaneous executions
  },
  wasm: {
    maxCacheSize: 100,        // Compiled WASM module cache
  },
  workers: {
    poolSize: 4,              // Deno worker pool size
    maxTasksPerWorker: 100,
    workerTimeout: 30000,
  },
  auth: {
    enabled: true,
    token: process.env.ISOLATE_AUTH_TOKEN || null,
    rateLimitPerMinute: 60,
  },
};
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ISOLATE_PORT` | 5900 | Server port |
| `ISOLATE_HOST` | 127.0.0.1 | Server bind address |
| `ISOLATE_AUTH_TOKEN` | null | Bearer token for auth (null = open) |
| `ISOLATE_LOG_LEVEL` | info | Logging level |

### Running as a systemd Service

```ini
[Unit]
Description=Isolate Service (V8/WASM Sandbox)
After=network.target

[Service]
Type=simple
User=shawn
WorkingDirectory=/home/shawn/isolate-service
ExecStart=/home/shawn/.local/bin/node src/server.js
Restart=on-failure
RestartSec=5
Environment=ISOLATE_AUTH_TOKEN=your-secret-token

[Install]
WantedBy=multi-user.target
```

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
├── agents/
│   ├── lp-sentinel.py      # Orca LP position monitor (5 min)
│   ├── infra-watchdog.py   # System services + Docker monitor (15 min)
│   ├── hashrate-monitor.py # XMRig mining performance monitor (15 min)
│   ├── docker-health.py    # Docker container health monitor (15 min)
│   └── honcho-vault.py     # System state snapshot archiver (30 min)
├── package.json
├── LICENSE
└── README.md
```

---

## Building Monitoring Agents

The agent pattern is simple and reusable. Here's how to build your own:

### 1. Data Collection (Python)

Fetch data from whatever APIs you need — Docker, cloud providers, on-chain data, whatever. The isolate can't do this for you (it has no network access), so the Python script handles it.

```python
def fetch_data():
    result = subprocess.run(
        ["curl", "-s", "http://your-api/endpoint"],
        capture_output=True, text=True, timeout=15
    )
    return json.loads(result.stdout)
```

### 2. Isolate Analysis (JavaScript)

Send the collected data into the isolate for threshold logic. This is where the "smarts" live — and because it runs in a sandbox, even bugs can't hurt your system.

```python
def analyze(data):
    payload = {
        "code": """(function(){
  const alerts = [];
  if (env.data.value < env.data.threshold) {
    alerts.push({level: 'WARNING', msg: 'Value below threshold'});
  }
  const status = alerts.length === 0 ? 'OK' : 'WARNING';
  return { alerts, status };
})()""",
        "preset": "compute",
        "env": { "data": data }
    }
    # Write payload to temp file (avoids shell escaping issues)
    with open('/tmp/agent-payload.json', 'w') as f:
        json.dump(payload, f)
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", "http://127.0.0.1:5900/execute",
         "-H", "Content-Type: application/json", "-d", "@/tmp/agent-payload.json"],
        capture_output=True, text=True, timeout=5
    )
    return json.loads(r.stdout)["result"]
```

### 3. Alert Delivery

Only alert when something's wrong. Silence = everything is OK.

```python
def main():
    data = fetch_data()
    result = analyze(data)

    if result["status"] == "OK":
        print("Agent: OK")
        sys.exit(0)     # Cron stays silent

    for alert in result["alerts"]:
        print(f"[{alert['level']}] {alert['msg']}")
    sys.exit(2 if result["status"] == "CRITICAL" else 1)
```

### Exit Codes

| Code | Meaning | Cron Behavior |
|------|---------|---------------|
| 0 | OK — no issues | No alert delivered |
| 1 | WARNING — needs attention soon | Alert delivered |
| 2 | CRITICAL — needs immediate action | Alert delivered immediately |

---

## Performance

Real workloads measured on a Ryzen 9 7945HX (16C/32T):

| Task | Time |
|------|------|
| API response filter (5 coins) | 0.87ms |
| LP position math (sqrt price, ticks, IL) | 0.92ms |
| XMRig log parsing (50 lines) | 1.03ms |
| Multi-level data pipeline (group/aggregate/rank) | 0.90ms |
| Encoding (btoa/atob) | 0.80ms |
| Large dataset aggregation (1,000 items) | 1.02ms |

Five agents checking every 5-15 minutes = ~2ms total per minute of compute time. The other 99.997% of each minute is spent waiting on network responses from the APIs the agents query.

---

## Limitations

- **V8 `vm` is not a security boundary** against determined attackers. Use Docker/Sandbox containers for truly untrusted code.
- **No filesystem access** inside isolates. If you need files, fetch them outside and pass data via `env`.
- **No network access** inside isolates (except `agent` preset with explicitly-provided `fetch`). All API calls must happen outside the sandbox.
- **WASM module cache is in-memory only** — lost on service restart. Recompile after restarts.
- **Deno Workers** have ~80ms spawn overhead per task. Use V8 isolates for quick <30s computations; use Deno for heavier jobs that benefit from real parallelism or TypeScript.
- **Memory bombs** can inflate the V8 heap before the timeout kills them. For production, consider adding `--max-old-space-size` as a V8 flag.

---

## Contributing

Issues and pull requests welcome. A few areas that could use help:

- **WASI support** — the current WASI stubs are minimal. Real WASI implementation would enable running Rust/C-compiled WASM programs.
- **Persistent WASM cache** — disk-backed cache that survives restarts.
- **Agent framework** — a more formalized plugin system for monitoring agents.
- **Prometheus metrics** — expose `/metrics` endpoint for Grafana integration.
- **WebSocket transport** — real-time execution results instead of HTTP polling.

---

## License

MIT — see [LICENSE](LICENSE).