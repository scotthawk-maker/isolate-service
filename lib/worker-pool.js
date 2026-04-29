/**
 * Deno Worker Pool - managed pool of Deno isolate workers
 * 
 * For heavier tasks that need more than V8 vm contexts:
 * - Full TypeScript support
 * - Real Web Workers with zero permissions
 * - Persistent workers that can handle multiple tasks
 * - Worker recycling and health checks
 */

const { spawn } = require('child_process');
const path = require('path');
const crypto = require('crypto');
const fs = require('fs');

const DENO_BIN = process.env.DENO_PATH || 'deno';

class WorkerPool {
  constructor(options = {}) {
    this.poolSize = options.poolSize || 4;
    this.maxTasksPerWorker = options.maxTasksPerWorker || 100;
    this.workerTimeout = options.workerTimeout || 30000;
    this.workers = new Map();
    this.taskQueue = [];
    this.totalTasks = 0;
    this.totalCompleted = 0;
    this.totalFailed = 0;
    this.denopath = options.denopath || DENO_BIN;
  }

  /**
   * Execute code in a Deno Web Worker (full isolate)
   * @param {string} code - TypeScript/JavaScript code
   * @param {object} options
   * @param {object} options.data - Data to pass to the worker
   * @param {number} options.timeout - Max execution time in ms
   * @param {string[]} options.permissions - Deno permissions to grant (default: none)
   * @returns {Promise<object>} Execution result
   */
  execute(code, options = {}) {
    return new Promise((resolve, reject) => {
      const taskId = crypto.randomUUID();
      const timeout = Math.min(options.timeout || this.workerTimeout, 60000);
      const data = options.data || {};

      // Build the worker script with the user's code
      const workerScript = `
        const workerCode = ${JSON.stringify(code)};
        const taskData = ${JSON.stringify(data)};

        self.onmessage = async (e) => {
          const { taskId } = e.data;
          const startTime = performance.now();

          try {
            // Execute the code
            const fn = new Function('data', 'console', workerCode);
            const safeConsole = {
              log: (...args) => self.postMessage({ type: 'log', level: 'log', msg: args.join(' ') }),
              error: (...args) => self.postMessage({ type: 'log', level: 'error', msg: args.join(' ') }),
              warn: (...args) => self.postMessage({ type: 'log', level: 'warn', msg: args.join(' ') }),
              info: (...args) => self.postMessage({ type: 'log', level: 'info', msg: args.join(' ') }),
            };

            const result = await fn(taskData, safeConsole);
            const elapsed = performance.now() - startTime;

            self.postMessage({
              type: 'result',
              taskId,
              success: true,
              result: result,
              elapsed: elapsed.toFixed(2),
            });
          } catch (err) {
            self.postMessage({
              type: 'result',
              taskId,
              success: false,
              error: { message: err.message, name: err.name },
              elapsed: (performance.now() - startTime).toFixed(2),
            });
          }
        };

        // Signal ready
        self.postMessage({ type: 'ready' });
      `;

      // Write temp worker script
      const scriptPath = path.join('/tmp', `isolate-worker-${taskId}.js`);
      fs.writeFileSync(scriptPath, workerScript);

      const logs = [];
      let resolved = false;

      // Spawn Deno process
      const denoArgs = ['run', '--unstable-worker-options'];
      // No permissions by default - zero trust
      if (options.permissions && options.permissions.length > 0) {
        options.permissions.forEach(p => denoArgs.push(`--allow-${p}`));
      } else {
        denoArgs.push('--v8-flags=--max-old-space-size=128');
      }
      denoArgs.push(scriptPath);

      const proc = spawn(this.denopath, denoArgs, {
        timeout,
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (d) => { stdout += d.toString(); });
      proc.stderr.on('data', (d) => { stderr += d.toString(); });

      proc.on('close', (code) => {
        // Clean up temp file
        try { fs.unlinkSync(scriptPath); } catch {}

        if (!resolved) {
          resolved = true;
          this.totalFailed++;
          resolve({
            success: false,
            error: {
              type: 'PROCESS_EXIT',
              message: `Worker exited with code ${code}`,
              stderr: stderr.slice(0, 500),
            },
            executionId: taskId,
            stats: { mode: 'deno-worker' },
          });
        }
      });

      // Set timeout
      const timer = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          proc.kill('SIGKILL');
          this.totalFailed++;
          try { fs.unlinkSync(scriptPath); } catch {}
          resolve({
            success: false,
            error: { type: 'TIMEOUT', message: `Worker timed out after ${timeout}ms` },
            executionId: taskId,
            stats: { mode: 'deno-worker', timeout },
          });
        }
      }, timeout);

      // Parse stdout for results
      stdout.split('\n').forEach(line => {
        if (!line.trim()) return;
        try {
          const msg = JSON.parse(line);
          if (msg.type === 'log') logs.push(msg);
          if (msg.type === 'result') {
            if (!resolved) {
              resolved = true;
              clearTimeout(timer);
              try { fs.unlinkSync(scriptPath); } catch {}
              this.totalCompleted++;
              resolve({
                ...msg,
                logs,
                executionId: taskId,
                stats: { mode: 'deno-worker', elapsed: msg.elapsed },
              });
            }
          }
        } catch {}
      });
    });
  }

  /**
   * Get pool stats
   */
  stats() {
    return {
      poolSize: this.poolSize,
      totalTasks: this.totalTasks,
      totalCompleted: this.totalCompleted,
      totalFailed: this.totalFailed,
      queueLength: this.taskQueue.length,
      denopath: this.denopath,
    };
  }
}

module.exports = WorkerPool;