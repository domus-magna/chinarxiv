/**
 * Cloudflare Pages Function: Figure Translation Request Handler
 *
 * Endpoint: POST /api/request-figure-translation
 * Body: { paper_id: "chinaxiv-202510.00001" }
 *
 * Features:
 * - Simple duplicate detection (60-second window per IP+paper)
 * - Request logging to KV storage
 * - Privacy-preserving IP hashing
 */
// TODO(v2, when request volume is steady in the hundreds/day): move ingestion into a Durable Object
// so dedupe + logging are atomic and batched. The DO can maintain the 60s window in-memory and
// flush to KV/R2 every N entries or seconds to avoid per-request KV writes and race windows.
// TODO(v3, when we need durable analytics feeds/monitoring): introduce a Queue/DO→R2 pipeline with
// per-day JSONL/Parquet rollups, alerting on write failures or abnormal spikes, and a small export
// endpoint for the figure batcher. Keep IP hashes, and add opt-in UA/referrer with schema guards.

/**
 * Hash a string using SHA-256
 * @param {string} str - String to hash
 * @returns {Promise<string>} Hex-encoded hash
 */
async function hashString(str) {
  const encoder = new TextEncoder();
  const data = encoder.encode(str);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Create response headers with CORS support
 * @param {string} origin - Request origin
 * @returns {Object} Headers object
 */
function createHeaders(origin) {
  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  // Explicit allowlist of origins
  const allowedOrigins = [
    'https://chinarxiv.org',
    'https://www.chinarxiv.org',
    'https://chinarxiv.com',  // Keep .com for legacy support
    'https://www.chinarxiv.com'
  ];

  // Exact match only (no substring matching)
  if (origin && allowedOrigins.includes(origin)) {
    headers['Access-Control-Allow-Origin'] = origin;
  }

  return headers;
}

/**
 * Handle OPTIONS request for CORS preflight
 */
export async function onRequestOptions(context) {
  const { request } = context;
  const origin = request.headers.get('Origin');

  return new Response(null, {
    status: 204,
    headers: createHeaders(origin)
  });
}

/**
 * Handle POST request for figure translation
 */
export async function onRequestPost(context) {
  const { request, env } = context;
  const origin = request.headers.get('Origin');

  try {
    // Parse request body
    const body = await request.json();
    const { paper_id } = body;

    // Validate paper_id
    if (!paper_id || typeof paper_id !== 'string') {
      return new Response(JSON.stringify({
        success: false,
        message: 'Invalid paper_id'
      }), {
        status: 400,
        headers: createHeaders(origin)
      });
    }

    // Validate paper_id format (chinaxiv-YYYYMM.NNNNN)
    if (!/^chinaxiv-\d{6}\.\d{5}$/.test(paper_id)) {
      return new Response(JSON.stringify({
        success: false,
        message: 'Invalid paper_id format'
      }), {
        status: 400,
        headers: createHeaders(origin)
      });
    }

    // Get IP address (Cloudflare provides this header)
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const ipHash = await hashString(ip);

    // Check for duplicate request (same IP + paper within 60 seconds)
    const dupKey = `dup:${ipHash}:${paper_id}`;
    const existing = await env.FIGURE_REQUESTS.get(dupKey);

    if (existing) {
      return new Response(JSON.stringify({
        success: false,
        message: 'Duplicate request detected. Please wait before requesting again.'
      }), {
        status: 409,
        headers: createHeaders(origin)
      });
    }

    // Mark as seen (with 60-second TTL for auto-cleanup)
    await env.FIGURE_REQUESTS.put(dupKey, Date.now().toString(), {
      expirationTtl: 60
    });

    // Log the request with unique per-request key to prevent race conditions
    const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
    const requestId = crypto.randomUUID();
    const logKey = `requests:${today}:${requestId}`;

    const entry = JSON.stringify({
      paper_id,
      timestamp: new Date().toISOString(),
      ip_hash: ipHash.substring(0, 16) // First 16 chars for privacy
    });

    // TODO(future): Automate aggregation and management of per-request keys
    // - Current: Manual aggregation via scripts/aggregate_figure_requests.py
    // - Keys auto-expire after 90 days (7776000 seconds)
    // - Future ideas:
    //   - Cron job to run aggregation daily/weekly
    //   - Cloudflare Workers Cron Trigger for automatic aggregation
    //   - Move to Durable Objects for real-time batching (no aggregation needed)
    //   - Archive to R2 for long-term analytics
    await env.FIGURE_REQUESTS.put(logKey, entry, {
      expirationTtl: 7776000  // 90 days in seconds
    });

    // Success response
    return new Response(JSON.stringify({
      success: true,
      message: 'Request logged successfully'
    }), {
      status: 200,
      headers: createHeaders(origin)
    });

  } catch (error) {
    console.error('Error processing request:', error);

    // Malformed JSON = client error (400)
    if (error instanceof SyntaxError) {
      return new Response(JSON.stringify({
        success: false,
        message: 'Invalid JSON in request body'
      }), {
        status: 400,
        headers: createHeaders(origin)
      });
    }

    // Other errors = server error (500)
    return new Response(JSON.stringify({
      success: false,
      message: 'Internal server error'
    }), {
      status: 500,
      headers: createHeaders(origin)
    });
  }
}
