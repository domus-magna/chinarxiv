/**
 * Backfill Dashboard API Worker
 *
 * Provides endpoints for tracking paper translation backfill progress.
 * Reads data from Backblaze B2 storage.
 */

import { AwsClient } from 'aws4fetch';

interface Env {
  // Auth credentials (set via wrangler secret put)
  ADMIN_USERNAME: string;
  ADMIN_PASSWORD: string;

  // B2 credentials (set via wrangler secret put)
  B2_KEY_ID: string;
  B2_APP_KEY: string;
  B2_ENDPOINT: string;

  // B2 config (set in wrangler.toml vars)
  B2_BUCKET: string;
  B2_PREFIX: string;
}

// Cache for B2 responses (5 min TTL)
const cache = new Map<string, { data: unknown; expires: number }>();
const CACHE_TTL_MS = 5 * 60 * 1000;

/**
 * Basic auth middleware
 */
function checkAuth(request: Request, env: Env): Response | null {
  const authorization = request.headers.get('Authorization');

  if (!authorization) {
    return new Response('Authentication required', {
      status: 401,
      headers: {
        'WWW-Authenticate': 'Basic realm="Backfill Dashboard"',
        'Content-Type': 'text/plain',
      },
    });
  }

  const [scheme, encoded] = authorization.split(' ');

  if (scheme !== 'Basic' || !encoded) {
    return new Response('Invalid authorization header', { status: 400 });
  }

  const decoded = atob(encoded);
  const [username, password] = decoded.split(':');

  if (username !== env.ADMIN_USERNAME || password !== env.ADMIN_PASSWORD) {
    return new Response('Invalid credentials', {
      status: 401,
      headers: {
        'WWW-Authenticate': 'Basic realm="Backfill Dashboard"',
      },
    });
  }

  return null; // Auth passed
}

/**
 * Create B2 S3-compatible client
 */
function createB2Client(env: Env): AwsClient {
  return new AwsClient({
    accessKeyId: env.B2_KEY_ID,
    secretAccessKey: env.B2_APP_KEY,
    service: 's3',
    region: 'us-west-004',
  });
}

/**
 * Fetch object from B2 with caching
 */
async function fetchB2Object(
  client: AwsClient,
  env: Env,
  key: string
): Promise<string | null> {
  const cacheKey = `b2:${key}`;
  const cached = cache.get(cacheKey);

  if (cached && cached.expires > Date.now()) {
    return cached.data as string;
  }

  const url = `${env.B2_ENDPOINT}/${env.B2_BUCKET}/${env.B2_PREFIX}${key}`;

  try {
    const response = await client.fetch(url);

    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      throw new Error(`B2 fetch failed: ${response.status}`);
    }

    const text = await response.text();
    cache.set(cacheKey, { data: text, expires: Date.now() + CACHE_TTL_MS });
    return text;
  } catch (error) {
    console.error(`Error fetching ${key}:`, error);
    return null;
  }
}

/**
 * List objects in B2 with prefix
 */
async function listB2Objects(
  client: AwsClient,
  env: Env,
  prefix: string
): Promise<string[]> {
  const cacheKey = `b2-list:${prefix}`;
  const cached = cache.get(cacheKey);

  if (cached && cached.expires > Date.now()) {
    return cached.data as string[];
  }

  const url = `${env.B2_ENDPOINT}/${env.B2_BUCKET}?list-type=2&prefix=${encodeURIComponent(env.B2_PREFIX + prefix)}`;

  try {
    const response = await client.fetch(url);

    if (!response.ok) {
      throw new Error(`B2 list failed: ${response.status}`);
    }

    const xml = await response.text();
    // Simple XML parsing for S3 ListObjectsV2 response
    const keys: string[] = [];
    const keyRegex = /<Key>([^<]+)<\/Key>/g;
    let match;
    while ((match = keyRegex.exec(xml)) !== null) {
      // Remove prefix to get relative key
      const fullKey = match[1];
      const relativeKey = fullKey.replace(env.B2_PREFIX, '');
      keys.push(relativeKey);
    }

    cache.set(cacheKey, { data: keys, expires: Date.now() + CACHE_TTL_MS });
    return keys;
  } catch (error) {
    console.error(`Error listing ${prefix}:`, error);
    return [];
  }
}

/**
 * Parse CSV content into array of objects
 */
function parseCSV(content: string): Record<string, string>[] {
  const lines = content.trim().split('\n');
  if (lines.length < 2) return [];

  const headers = lines[0].split(',').map(h => h.trim());
  const rows: Record<string, string>[] = [];

  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(',').map(v => v.trim());
    const row: Record<string, string> = {};
    headers.forEach((header, index) => {
      row[header] = values[index] || '';
    });
    rows.push(row);
  }

  return rows;
}

/**
 * GET /api/backfill/status
 * Returns current backfill progress statistics
 */
async function handleStatus(env: Env): Promise<Response> {
  const client = createB2Client(env);

  // List all run summary CSVs to calculate totals
  const runFiles = await listB2Objects(client, env, 'indexes/runs/');

  let totalAttempted = 0;
  let totalValidated = 0;
  let totalFlagged = 0;
  let totalCost = 0;
  let lastUpdated = '';

  // Parse each run summary CSV
  for (const key of runFiles) {
    if (!key.endsWith('.csv')) continue;

    const content = await fetchB2Object(client, env, key);
    if (!content) continue;

    const rows = parseCSV(content);
    for (const row of rows) {
      totalAttempted += parseInt(row.selected_count || '0', 10);
      totalValidated += parseInt(row.validated_ok || '0', 10);
      totalFlagged += parseInt(row.flagged_count || '0', 10);
      totalCost += parseFloat(row.total_cost_usd || '0');

      // Track most recent completion time
      if (row.completed_at && row.completed_at > lastUpdated) {
        lastUpdated = row.completed_at;
      }
    }
  }

  // Calculate pending (estimated from harvest records - we'll refine this)
  // For now, use attempted as total since that's what's been selected
  const pending = Math.max(0, totalAttempted - totalValidated - totalFlagged);
  const progressPercent = totalAttempted > 0
    ? Math.round((totalValidated / totalAttempted) * 100)
    : 0;

  const status = {
    total_papers: totalAttempted,
    translated: totalValidated,
    flagged: totalFlagged,
    failed: 0, // Would need separate tracking
    pending: pending,
    progress_percent: progressPercent,
    total_cost_usd: Math.round(totalCost * 100) / 100,
    last_updated: lastUpdated || new Date().toISOString(),
  };

  return new Response(JSON.stringify(status, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=60',
    },
  });
}

/**
 * GET /api/backfill/history?days=30
 * Returns historical progress data by date
 */
async function handleHistory(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const days = parseInt(url.searchParams.get('days') || '30', 10);

  const client = createB2Client(env);
  const runFiles = await listB2Objects(client, env, 'indexes/runs/');

  // Aggregate by date
  const byDate = new Map<string, {
    papers_added: number;
    papers_translated: number;
    papers_flagged: number;
    cost_usd: number;
  }>();

  for (const key of runFiles) {
    if (!key.endsWith('.csv')) continue;

    const content = await fetchB2Object(client, env, key);
    if (!content) continue;

    const rows = parseCSV(content);
    for (const row of rows) {
      const date = (row.completed_at || '').slice(0, 10);
      if (!date) continue;

      const existing = byDate.get(date) || {
        papers_added: 0,
        papers_translated: 0,
        papers_flagged: 0,
        cost_usd: 0,
      };

      existing.papers_added += parseInt(row.selected_count || '0', 10);
      existing.papers_translated += parseInt(row.validated_ok || '0', 10);
      existing.papers_flagged += parseInt(row.flagged_count || '0', 10);
      existing.cost_usd += parseFloat(row.total_cost_usd || '0');

      byDate.set(date, existing);
    }
  }

  // Convert to array and sort by date
  const history = Array.from(byDate.entries())
    .map(([date, data]) => ({
      date,
      ...data,
      cost_usd: Math.round(data.cost_usd * 100) / 100,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));

  // Calculate cumulative totals
  let cumulative = 0;
  const historyWithCumulative = history.map(entry => {
    cumulative += entry.papers_translated;
    return {
      ...entry,
      cumulative_total: cumulative,
    };
  });

  // Filter to requested days
  const cutoffDate = new Date();
  cutoffDate.setDate(cutoffDate.getDate() - days);
  const cutoffStr = cutoffDate.toISOString().slice(0, 10);

  const filtered = historyWithCumulative.filter(entry => entry.date >= cutoffStr);

  return new Response(JSON.stringify(filtered, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=300',
    },
  });
}

/**
 * Main request handler
 */
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers for dashboard
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    };

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // Check auth for all API routes
    const authError = checkAuth(request, env);
    if (authError) {
      // Add CORS headers to auth error
      authError.headers.set('Access-Control-Allow-Origin', '*');
      return authError;
    }

    // Route handling
    try {
      let response: Response;

      if (path === '/api/backfill/status' || path === '/status') {
        response = await handleStatus(env);
      } else if (path === '/api/backfill/history' || path === '/history') {
        response = await handleHistory(request, env);
      } else if (path === '/api/backfill/health' || path === '/health' || path === '/') {
        response = new Response(JSON.stringify({
          status: 'ok',
          timestamp: new Date().toISOString(),
          version: '1.0.0',
        }), {
          headers: { 'Content-Type': 'application/json' },
        });
      } else if (path === '/debug') {
        // Debug endpoint to check B2 connectivity
        const client = createB2Client(env);
        const listUrl = `${env.B2_ENDPOINT}/${env.B2_BUCKET}?list-type=2&prefix=indexes/runs/&max-keys=10`;
        let debugInfo: any = {
          bucket: env.B2_BUCKET,
          prefix: env.B2_PREFIX,
          endpoint: env.B2_ENDPOINT ? env.B2_ENDPOINT.substring(0, 30) + '...' : 'NOT SET',
          keyIdSet: !!env.B2_KEY_ID,
          appKeySet: !!env.B2_APP_KEY,
          listUrl: listUrl.substring(0, 80) + '...',
        };
        try {
          const listResp = await client.fetch(listUrl);
          debugInfo.listStatus = listResp.status;
          debugInfo.listOk = listResp.ok;
          if (listResp.ok) {
            const xml = await listResp.text();
            debugInfo.xmlLength = xml.length;
            debugInfo.xmlPreview = xml.substring(0, 500);
          } else {
            debugInfo.errorBody = await listResp.text();
          }
        } catch (e: any) {
          debugInfo.error = e.message;
        }
        response = new Response(JSON.stringify(debugInfo, null, 2), {
          headers: { 'Content-Type': 'application/json' },
        });
      } else {
        response = new Response(JSON.stringify({ error: 'Not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      // Add CORS headers to response
      Object.entries(corsHeaders).forEach(([key, value]) => {
        response.headers.set(key, value);
      });

      return response;
    } catch (error) {
      console.error('Request error:', error);
      return new Response(JSON.stringify({
        error: 'Internal server error',
        message: error instanceof Error ? error.message : 'Unknown error',
      }), {
        status: 500,
        headers: {
          'Content-Type': 'application/json',
          ...corsHeaders,
        },
      });
    }
  },
};
