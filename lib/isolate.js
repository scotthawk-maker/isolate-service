/**
 * Isolate Engine - Core V8 sandbox execution
 * 
 * Runs JavaScript code in an isolated V8 context with:
 * - Configurable timeout (kills runaway code)
 * - Selective API exposure (only what you allow)
 * - Memory monitoring
 * - Pre/post hooks
 * - Multiple context types (minimal, compute, data, agent)
 */

const vm = require('vm');
const crypto = require('crypto');

// Context presets - what APIs are available in each mode
const CONTEXT_PRESETS = {
  minimal: {
    description: 'Pure compute - no APIs at all',
    apis: {}
  },
  compute: {
    description: 'Math and basic data structures',
    apis: {
      Math: Math,
      JSON: JSON,
      parseInt: parseInt,
      parseFloat: parseFloat,
      isNaN: isNaN,
      isFinite: isFinite,
      Array: Array,
      Object: Object,
      String: String,
      Number: Number,
      Boolean: Boolean,
      Date: Date,
      Map: Map,
      Set: Set,
      Promise: Promise,
      Symbol: Symbol,
      RegExp: RegExp,
      Error: Error,
      TypeError: TypeError,
      RangeError: RangeError,
    }
  },
  data: {
    description: 'Data processing - compute + encoding/hashing',
    apis: {
      // All compute APIs plus:
      Buffer: Buffer,
      TextEncoder: TextEncoder,
      TextDecoder: TextDecoder,
      btoa: (str) => Buffer.from(str, 'binary').toString('base64'),
      atob: (b64) => Buffer.from(b64, 'base64').toString('binary'),
      crypto: {
        getRandomValues: (arr) => crypto.randomFillSync(arr),
        subtle: {
          digest: async (algo, data) => {
            const hash = crypto.createHash(algo.replace('-', '').toLowerCase());
            hash.update(data);
            return hash.digest().buffer;
          }
        }
      }
    }
  },
  agent: {
    description: 'Agent execution - data + controlled HTTP fetch + logging',
    apis: {
      // Custom fetch that logs requests for audit
      fetch: null,  // Must be explicitly provided per-execution
      console: null, // Must be explicitly provided per-execution
    }
  }
};

// Merge preset APIs: minimal < compute < data < agent (inherits upward)
function buildPresetAPIs(presetName) {
  const preset = CONTEXT_PRESETS[presetName];
  if (!preset) throw new Error(`Unknown preset: ${presetName}`);

  // Start with compute as base for all non-minimal presets
  if (presetName === 'minimal') return {};
  
  const apis = { ...CONTEXT_PRESETS.compute.apis };
  
  if (presetName === 'data' || presetName === 'agent') {
    Object.assign(apis, CONTEXT_PRESETS.data.apis);
  }
  
  if (presetName === 'agent') {
    // Agent preset APIs are added per-execution (fetch, console)
    Object.assign(apis, CONTEXT_PRESETS.agent.apis);
  }
  
  return apis;
}

class IsolateEngine {
  constructor(options = {}) {
    this.defaultTimeout = options.defaultTimeout || 5000;    // 5s
    this.maxTimeout = options.maxTimeout || 30000;            // 30s hard cap
    this.maxMemoryMB = options.maxMemoryMB || 128;            // 128MB
    this.maxConcurrent = options.maxConcurrent || 50;
    this.activeCount = 0;
    this.totalExecutions = 0;
    this.totalBlocked = 0;
    this.executionLog = [];
  }

  /**
   * Execute code in an isolated V8 context
   * @param {string} code - JavaScript code to execute
   * @param {object} options
   * @param {string} options.preset - Context preset: minimal, compute, data, agent
   * @param {object} options.env - Custom environment variables/data to inject
   * @param {object} options.apis - Additional APIs to expose (overrides preset)
   * @param {number} options.timeout - Max execution time in ms
   * @param {string} options.language - 'javascript' (default) or 'wasm'
   * @param {function} options.onLog - Callback for console/log output
   * @returns {object} { success, result, error, logs, stats }
   */
  execute(code, options = {}) {
    const {
      preset = 'compute',
      env = {},
      apis: customApis = {},
      timeout = this.defaultTimeout,
      onLog = null,
      language = 'javascript',
    } = options;

    const cappedTimeout = Math.min(timeout, this.maxTimeout);
    const executionId = crypto.randomUUID();
    const startTime =_hr_time();

    // Check concurrency
    if (this.activeCount >= this.maxConcurrent) {
      this.totalBlocked++;
      return {
        success: false,
        error: 'Max concurrent executions reached',
        executionId,
        stats: { queued: false, rejected: true }
      };
    }

    this.activeCount++;
    this.totalExecutions++;

    // Build the sandbox
    const baseApis = buildPresetAPIs(preset);
    const sandbox = { ...baseApis, ...customApis };

    // Inject environment data
    if (env && Object.keys(env).length > 0) {
      sandbox.env = env;
      sandbox.input = env;  // Alias for convenience
    }

    // Set up logging capture
    const logs = [];
    if (!sandbox.console) {
      sandbox.console = {
        log: (...args) => {
          const msg = args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ');
          logs.push({ level: 'log', message: msg, time: Date.now() });
          if (onLog) onLog('log', msg);
        },
        error: (...args) => {
          const msg = args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ');
          logs.push({ level: 'error', message: msg, time: Date.now() });
          if (onLog) onLog('error', msg);
        },
        warn: (...args) => {
          const msg = args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ');
          logs.push({ level: 'warn', message: msg, time: Date.now() });
          if (onLog) onLog('warn', msg);
        },
        info: (...args) => {
          const msg = args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ');
          logs.push({ level: 'info', message: msg, time: Date.now() });
          if (onLog) onLog('info', msg);
        },
      };
    }

    try {
      const context = vm.createContext(sandbox);

      // Freeze built-in prototypes to prevent cross-context pollution attacks
      // Object.prototype.polluted = true would otherwise leak to other isolates
      vm.runInContext(`
        Object.freeze(Object.prototype);
        Object.freeze(Array.prototype);
        Object.freeze(Function.prototype);
        Object.freeze(String.prototype);
        Object.freeze(Number.prototype);
        Object.freeze(Boolean.prototype);
      `, context, { timeout: 100 });

      // Wrap code in IIFE if it uses return statements
      let wrappedCode = code;
      if (code.includes('return ') && !code.trim().startsWith('(function') && !code.trim().startsWith('function')) {
        wrappedCode = `(function() { ${code} })()`;
      }

      const script = new vm.Script(wrappedCode, {
        filename: `isolate-${executionId}.js`,
        lineOffset: 0,
      });

      const result = script.runInContext(context, {
        timeout: cappedTimeout,
        microtaskMode: 'afterEvaluate',  // Allow promises to resolve
      });

      const elapsed = _hr_elapsed(startTime);
      this.activeCount--;

      const execRecord = {
        executionId,
        preset,
        timeout: cappedTimeout,
        elapsed,
        success: true,
        logCount: logs.length,
        codeLength: code.length,
      };
      this.executionLog.push(execRecord);
      if (this.executionLog.length > 1000) this.executionLog.shift();

      return {
        success: true,
        result: _serialize(result),
        logs,
        executionId,
        stats: {
          elapsed,
          timeout: cappedTimeout,
          preset,
          logCount: logs.length,
          memoryUsage: process.memoryUsage(),
        }
      };

    } catch (err) {
      const elapsed = _hr_elapsed(startTime);
      this.activeCount--;

      const isTimeout = err.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT' ||
                        err.message?.includes('timed out');
      const isSecurity = err.name === 'ReferenceError' ||
                         err.name === 'TypeError';

      this.executionLog.push({
        executionId,
        preset,
        timeout: cappedTimeout,
        elapsed,
        success: false,
        errorType: isTimeout ? 'timeout' : isSecurity ? 'security' : 'runtime',
        errorMessage: err.message?.split('\n')[0],
      });
      if (this.executionLog.length > 1000) this.executionLog.shift();

      return {
        success: false,
        error: {
          type: isTimeout ? 'TIMEOUT' : isSecurity ? 'SECURITY' : 'RUNTIME',
          message: err.message?.split('\n')[0],
          name: err.name,
        },
        logs,
        executionId,
        stats: {
          elapsed,
          timeout: cappedTimeout,
          preset,
          logCount: logs.length,
        }
      };
    }
  }

  /**
   * Validate code without executing (syntax check)
   */
  validate(code) {
    let checkCode = code;
    if (checkCode.includes('return ') && !checkCode.trim().startsWith('(function') && !checkCode.trim().startsWith('function')) {
      checkCode = `(function() { ${checkCode} })()`;
    }
    try {
      new vm.Script(checkCode, { filename: 'validate.js' });
      return { valid: true };
    } catch (err) {
      return {
        valid: false,
        error: err.message?.split('\n')[0],
        line: err.lineNumber,
        column: err.columnNumber,
      };
    }
  }

  /**
   * Get engine stats
   */
  stats() {
    const recent = this.executionLog.slice(-100);
    const successCount = recent.filter(e => e.success).length;
    const avgElapsed = recent.length > 0
      ? recent.reduce((sum, e) => sum + e.elapsed, 0) / recent.length
      : 0;
    const timeouts = recent.filter(e => e.errorType === 'timeout').length;
    const securityBlocks = recent.filter(e => e.errorType === 'security').length;

    return {
      active: this.activeCount,
      totalExecutions: this.totalExecutions,
      totalBlocked: this.totalBlocked,
      recentSuccessRate: recent.length > 0 ? (successCount / recent.length * 100).toFixed(1) : 0,
      recentAvgElapsed: avgElapsed.toFixed(2),
      recentTimeouts: timeouts,
      recentSecurityBlocks: securityBlocks,
      uptime: process.uptime(),
      memory: process.memoryUsage(),
    };
  }
}

// High-res timer
function _hr_time() {
  const [s, ns] = process.hrtime();
  return s * 1e6 + ns / 1e3; // microseconds
}

function _hr_elapsed(start) {
  const [s, ns] = process.hrtime();
  const now = s * 1e6 + ns / 1e3;
  return (now - start) / 1000; // ms
}

// Serialize result for JSON transport (handle circular refs, functions, etc)
function _serialize(obj, depth = 0) {
  if (depth > 5) return '[max depth]';
  if (obj === undefined) return null;
  if (obj === null || typeof obj !== 'object') return obj;
  if (typeof obj === 'function') return '[Function]';
  if (obj instanceof Error) return { name: obj.name, message: obj.message };
  if (Buffer.isBuffer(obj)) return `[Buffer ${obj.length} bytes]`;
  if (Array.isArray(obj)) return obj.map(v => _serialize(v, depth + 1));
  
  const result = {};
  for (const key of Object.keys(obj).slice(0, 50)) {
    result[key] = _serialize(obj[key], depth + 1);
  }
  return result;
}

// Make preset APIs available externally
IsolateEngine.PRESETS = CONTEXT_PRESETS;

module.exports = IsolateEngine;