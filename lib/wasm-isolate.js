/**
 * WASM Isolate Engine - compile & run WASM modules in sandboxed contexts
 * 
 * WASM is the most locked-down isolate possible:
 * - No filesystem, no network, no DOM by default
 * - Only gets imports you explicitly provide
 * - Runs at near-native speed
 * - Memory is bounded by the WASM memory definition
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

class WasmIsolate {
  constructor(options = {}) {
    this.cache = new Map();  // compiled module cache
    this.maxCacheSize = options.maxCacheSize || 100;
    this.totalCompiles = 0;
    this.totalExecutions = 0;
  }

  /**
   * Compile a WASM module from bytes or file
   * @param {Buffer|Uint8Array|string} source - WASM binary bytes or file path
   * @param {object} options
   * @returns {string} moduleId for future reference
   */
  async compile(source, options = {}) {
    let bytes;
    const sourceId = options.id || crypto.randomUUID();

    if (typeof source === 'string') {
      // It's a file path
      bytes = fs.readFileSync(source);
    } else {
      bytes = new Uint8Array(source);
    }

    const startTime = process.hrtime.bigint();
    const module = await WebAssembly.compile(bytes);
    const elapsed = Number(process.hrtime.bigint() - startTime) / 1e6;

    // Cache the compiled module
    this.cache.set(sourceId, {
      module,
      bytes: bytes.length,
      compiledAt: Date.now(),
      compileTime: elapsed,
    });

    // Evict old entries if cache is full
    if (this.cache.size > this.maxCacheSize) {
      const oldest = this.cache.keys().next().value;
      this.cache.delete(oldest);
    }

    this.totalCompiles++;

    return {
      moduleId: sourceId,
      byteSize: bytes.length,
      compileTime: elapsed.toFixed(2) + 'ms',
      exports: WebAssembly.Module.exports(module).map(e => ({
        name: e.name,
        kind: e.kind,
      })),
      imports: WebAssembly.Module.imports(module).map(i => ({
        module: i.module,
        name: i.name,
        kind: i.kind,
      })),
    };
  }

  /**
   * Execute a compiled WASM module
   * @param {string} moduleId - ID from compile()
   * @param {object} options
   * @param {object} options.imports - Custom WASM imports to provide
   * @param {object} options.args - Arguments to pass to the entry function
   * @param {string} options.entry - Export name to call (default: first function)
   * @returns {object} Execution result
   */
  async execute(moduleId, options = {}) {
    const cached = this.cache.get(moduleId);
    if (!cached) {
      return {
        success: false,
        error: `Module ${moduleId} not found. Compile it first.`,
      };
    }

    const { imports: customImports = {}, args = [], entry = null } = options;
    this.totalExecutions++;

    try {
      // Build import object - WASM needs these to operate
      const importObject = this._buildImports(cached.module, customImports);

      const startTime = process.hrtime.bigint();
      const instance = await WebAssembly.instantiate(cached.module, importObject);
      const instantiateTime = Number(process.hrtime.bigint() - startTime) / 1e6;

      // Find the entry function
      const exports = instance.exports;
      const entryFunc = entry
        ? exports[entry]
        : Object.values(exports).find(e => typeof e === 'function');

      if (!entryFunc) {
        return {
          success: false,
          error: 'No callable export found',
          exports: Object.keys(exports),
        };
      }

      const callStart = process.hrtime.bigint();
      const result = entryFunc(...args);
      const callTime = Number(process.hrtime.bigint() - callStart) / 1e6;

      // Get memory stats if available
      const memory = exports.memory;
      const memoryPages = memory ? memory.buffer.byteLength / 65536 : 0;

      return {
        success: true,
        result,
        stats: {
          instantiateTime: instantiateTime.toFixed(3) + 'ms',
          callTime: callTime.toFixed(3) + 'ms',
          memoryPages,
          memoryBytes: memoryPages * 65536,
          exports: Object.keys(exports),
        }
      };

    } catch (err) {
      return {
        success: false,
        error: {
          type: 'WASM_RUNTIME',
          message: err.message,
          name: err.name,
        },
      };
    }
  }

  /**
   * Build WASM import object with safe defaults
   */
  _buildImports(module, customImports) {
    const requiredImports = WebAssembly.Module.imports(module);
    const importObject = {};

    for (const imp of requiredImports) {
      // Check if custom import provides this
      if (customImports[imp.module] && customImports[imp.module][imp.name] !== undefined) {
        if (!importObject[imp.module]) importObject[imp.module] = {};
        importObject[imp.module][imp.name] = customImports[imp.module][imp.name];
        continue;
      }

      // Provide safe defaults for common WASI imports
      if (imp.module === 'wasi_snapshot_preview1') {
        if (!importObject.wasi_snapshot_preview1) {
          importObject.wasi_snapshot_preview1 = this._wasiStubs();
        }
        continue;
      }

      // Unknown import - provide a zero/default stub
      if (!importObject[imp.module]) importObject[imp.module] = {};
      if (imp.kind === 'function') {
        importObject[imp.module][imp.name] = () => 0;
      } else if (imp.kind === 'memory') {
        importObject[imp.module][imp.name] = new WebAssembly.Memory({ initial: 1 });
      } else if (imp.kind === 'table') {
        importObject[imp.module][imp.name] = new WebAssembly.Table({ initial: 1, element: 'anyfunc' });
      } else {
        importObject[imp.module][imp.name] = 0;
      }
    }

    return importObject;
  }

  /**
   * WASI stubs - enough to run simple WASM programs
   */
  _wasiStubs() {
    return {
      proc_exit: () => {},
      fd_write: () => 0,
      fd_read: () => 0,
      fd_close: () => 0,
      fd_seek: () => 0,
      fd_fdstat_get: () => 0,
      environ_get: () => 0,
      environ_sizes_get: () => 0,
      args_get: () => 0,
      args_sizes_get: () => 0,
      random_get: (ptr, len) => {
        // Safe random for WASM
        return 0;
      },
      clock_time_get: () => BigInt(Date.now()) * 1000000n,
    };
  }

  /**
   * Get stats
   */
  stats() {
    return {
      cachedModules: this.cache.size,
      maxCacheSize: this.maxCacheSize,
      totalCompiles: this.totalCompiles,
      totalExecutions: this.totalExecutions,
      modules: Array.from(this.cache.entries()).map(([id, m]) => ({
        id,
        bytes: m.bytes,
        compileTime: m.compileTime.toFixed(2) + 'ms',
        age: Date.now() - m.compiledAt,
      })),
    };
  }

  /**
   * Clear cache
   */
  clearCache() {
    this.cache.clear();
    return { cleared: true };
  }
}

module.exports = WasmIsolate;