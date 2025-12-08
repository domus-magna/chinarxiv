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
 * Handle POST request for figure translation
 */
export async function onRequestPost(context) {
  const { request, env } = context;

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
        headers: { 'Content-Type': 'application/json' }
      });
    }

    // Validate paper_id format (chinaxiv-YYYYMM.NNNNN)
    if (!/^chinaxiv-\d{6}\.\d{5}$/.test(paper_id)) {
      return new Response(JSON.stringify({
        success: false,
        message: 'Invalid paper_id format'
      }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' }
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
        headers: { 'Content-Type': 'application/json' }
      });
    }

    // Mark as seen (with 60-second TTL for auto-cleanup)
    await env.FIGURE_REQUESTS.put(dupKey, Date.now().toString(), {
      expirationTtl: 60
    });

    // Log the request to daily log
    const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
    const logKey = `requests:${today}`;

    const entry = JSON.stringify({
      paper_id,
      timestamp: new Date().toISOString(),
      ip_hash: ipHash.substring(0, 16) // First 16 chars for privacy
    }) + '\n';

    // Append to existing log
    const existingLog = await env.FIGURE_REQUESTS.get(logKey) || '';
    await env.FIGURE_REQUESTS.put(logKey, existingLog + entry);

    // Success response
    return new Response(JSON.stringify({
      success: true,
      message: 'Request logged successfully'
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });

  } catch (error) {
    console.error('Error processing request:', error);
    return new Response(JSON.stringify({
      success: false,
      message: 'Internal server error'
    }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
