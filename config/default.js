/**
 * Default configuration for the isolate service
 */

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
    token: process.env.ISOLATE_AUTH_TOKEN || null,  // Bearer token (null = no auth)
    rateLimitPerMinute: 60,    // Requests per token per minute
  },

  logging: {
    level: process.env.ISOLATE_LOG_LEVEL || 'info',
    maxLogEntries: 500,        // Max logs kept per execution
  },
};