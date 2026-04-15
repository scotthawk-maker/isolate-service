/**
 * Authentication and rate limiting middleware
 */

const config = require('../config/default');

const rateLimitStore = new Map();

/**
 * Check auth token if enabled
 */
function authMiddleware(req, res) {
  if (!config.auth.enabled || !config.auth.token) return true;

  const auth = req.headers['authorization'];
  if (!auth || !auth.startsWith('Bearer ')) {
    res.writeHead(401, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Unauthorized — Bearer token required' }));
    return false;
  }

  const token = auth.slice(7);
  if (token !== config.auth.token) {
    res.writeHead(403, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Forbidden — invalid token' }));
    return false;
  }

  // Rate limiting
  const key = token.slice(0, 8);
  const now = Date.now();
  const record = rateLimitStore.get(key) || { count: 0, windowStart: now };

  if (now - record.windowStart > 60000) {
    record.count = 0;
    record.windowStart = now;
  }

  record.count++;
  rateLimitStore.set(key, record);

  if (record.count > config.auth.rateLimitPerMinute) {
    res.writeHead(429, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Rate limit exceeded', retryAfter: 60 }));
    return false;
  }

  return true;
}

module.exports = { authMiddleware };