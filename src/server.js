/**
 * Isolate Service — HTTP API server
 * 
 * Lightweight isolate execution service inspired by Cloudflare Dynamic Workers.
 * Runs JavaScript in sandboxed V8 isolates and WASM modules with zero-trust security.
 * 
 * Endpoints:
 *   POST /execute     - Execute code in a V8 isolate
 *   POST /wasm/compile - Compile a WASM module
 *   POST /wasm/run    - Execute a compiled WASM module
 *   POST /validate    - Validate code syntax without executing
 *   POST /deno        - Execute code in a Deno Web Worker (heavier, full TS support)
 *   GET  /health      - Service health check
 *   GET  /stats       - Engine statistics
 */

const http = require('http');
const { URL } = require('url');
const config = require('../config/default');
const IsolateEngine = require('../lib/isolate');
const WasmIsolate = require('../lib/wasm-isolate');
const WorkerPool = require('../lib/worker-pool');
const { authMiddleware } = require('../routes/auth');

const isolate = new IsolateEngine(config.engine);
const wasmIsolate = new WasmIsolate(config.wasm);
const workerPool = new WorkerPool(config.workers);

const server = http.createServer(async (req, res) => {
  const startTime = Date.now();
  const parsedUrl = new URL(req.url, `http://${req.headers.host}`);
  const pathname = parsedUrl.pathname;

  // CORS for local dev
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    return res.end();
  }

  // Auth check on all routes except health
  if (pathname !== '/health' && !authMiddleware(req, res)) return;

  try {
    // ─── ROUTES ───

    if (req.method === 'GET' && pathname === '/health') {
      return sendJSON(res, 200, {
        status: 'ok',
        uptime: Math.floor(process.uptime()),
        version: '0.1.0',
        engine: isolate.stats(),
        wasm: wasmIsolate.stats(),
        workers: workerPool.stats(),
      });
    }

    if (req.method === 'GET' && pathname === '/stats') {
      return sendJSON(res, 200, {
        engine: isolate.stats(),
        wasm: wasmIsolate.stats(),
        workers: workerPool.stats(),
        presets: Object.keys(IsolateEngine.PRESETS).map(k => ({
          name: k,
          description: IsolateEngine.PRESETS[k].description,
        })),
      });
    }

    if (req.method === 'POST' && pathname === '/execute') {
      const body = await readBody(req);
      const params = parseJSON(body);
      if (!params) return sendJSON(res, 400, { error: 'Invalid JSON body' });

      if (!params.code) return sendJSON(res, 400, { error: 'Missing "code" field' });

      const result = isolate.execute(params.code, {
        preset: params.preset || 'compute',
        env: params.env,
        apis: params.apis,
        timeout: params.timeout,
        language: params.language,
      });

      return sendJSON(res, result.success ? 200 : 422, {
        ...result,
        _meta: { elapsed: Date.now() - startTime + 'ms' },
      });
    }

    if (req.method === 'POST' && pathname === '/validate') {
      const body = await readBody(req);
      const params = parseJSON(body);
      if (!params) return sendJSON(res, 400, { error: 'Invalid JSON body' });
      if (!params.code) return sendJSON(res, 400, { error: 'Missing "code" field' });

      const result = isolate.validate(params.code);
      return sendJSON(res, result.valid ? 200 : 422, result);
    }

    if (req.method === 'POST' && pathname === '/wasm/compile') {
      const body = await readBody(req);
      const params = parseJSON(body);
      if (!params) return sendJSON(res, 400, { error: 'Invalid JSON body' });

      let source;
      if (params.bytes) {
        source = Buffer.from(params.bytes, 'base64');
      } else if (params.file) {
        source = params.file;
      } else {
        return sendJSON(res, 400, { error: 'Provide "bytes" (base64) or "file" path' });
      }

      const result = await wasmIsolate.compile(source, { id: params.id });
      return sendJSON(res, result.moduleId ? 200 : 500, result);
    }

    if (req.method === 'POST' && pathname === '/wasm/run') {
      const body = await readBody(req);
      const params = parseJSON(body);
      if (!params) return sendJSON(res, 400, { error: 'Invalid JSON body' });
      if (!params.moduleId) return sendJSON(res, 400, { error: 'Missing "moduleId" — compile first' });

      const result = await wasmIsolate.execute(params.moduleId, {
        imports: params.imports,
        args: params.args,
        entry: params.entry,
      });

      return sendJSON(res, result.success ? 200 : 422, result);
    }

    if (req.method === 'POST' && pathname === '/deno') {
      const body = await readBody(req);
      const params = parseJSON(body);
      if (!params) return sendJSON(res, 400, { error: 'Invalid JSON body' });
      if (!params.code) return sendJSON(res, 400, { error: 'Missing "code" field' });

      const result = await workerPool.execute(params.code, {
        data: params.data,
        timeout: params.timeout,
        permissions: params.permissions,
      });

      return sendJSON(res, result.success ? 200 : 422, {
        ...result,
        _meta: { elapsed: Date.now() - startTime + 'ms' },
      });
    }

    // 404
    return sendJSON(res, 404, {
      error: 'Not found',
      endpoints: ['/execute', '/validate', '/wasm/compile', '/wasm/run', '/deno', '/health', '/stats'],
    });

  } catch (err) {
    console.error('[SERVER ERROR]', err);
    return sendJSON(res, 500, { error: 'Internal server error', message: err.message });
  }
});

// Helpers
function sendJSON(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => resolve(body));
    req.on('error', reject);
    // Cap body size at 1MB
    setTimeout(() => {
      if (!body) resolve('');
    }, 5000);
    if (req.headers['content-length'] && parseInt(req.headers['content-length']) > 1048576) {
      reject(new Error('Body too large (max 1MB)'));
    }
  });
}

function parseJSON(str) {
  try {
    return JSON.parse(str);
  } catch {
    return null;
  }
}

// Start
const PORT = config.server.port;
const HOST = config.server.host;

server.listen(PORT, HOST, () => {
  console.log(`\n╔══════════════════════════════════════╗`);
  console.log(`║   ISOLATE SERVICE v0.1.0             ║`);
  console.log(`║   http://${HOST}:${PORT}                ║`);
  console.log(`║   Presets: minimal, compute, data,  ║`);
  console.log(`║           agent                       ║`);
  console.log(`║   Auth: ${config.auth.enabled && config.auth.token ? 'Bearer token' : 'Open (no token set)'}         ║`);
  console.log(`╚══════════════════════════════════════╝\n`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('Shutting down...');
  server.close(() => process.exit(0));
});
process.on('SIGINT', () => {
  console.log('Shutting down...');
  server.close(() => process.exit(0));
});