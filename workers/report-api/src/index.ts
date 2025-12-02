/**
 * ChinaRxiv Report API Worker
 *
 * Receives user problem reports from the website and creates GitHub issues
 * in the private chinarxiv-reports repository.
 */

// ============================================================================
// Types (exported for testing)
// ============================================================================

export interface Env {
  TURNSTILE_SECRET?: string;  // Optional for local dev
  GITHUB_TOKEN: string;
  GITHUB_REPO: string;
}

export interface ReportPayload {
  type: string;
  description: string;
  context: {
    url: string;
    paperId: string | null;
    paperTitle: string | null;
    consoleLogs: Array<{ level: string; msg: unknown[]; ts: number }>;
    userAgent: string;
    viewport: { w: number; h: number };
    timestamp: string;
    referrer: string;
  };
  turnstileToken?: string;
}

export interface TurnstileResponse {
  success: boolean;
  'error-codes'?: string[];
}

export interface RateLimitRecord {
  count: number;
  resetTime: number;
}

export type RateLimitStore = Map<string, RateLimitRecord>;

export interface ValidationResult {
  valid: boolean;
  error?: string;
  statusCode?: number;
}

export interface SanitizedLog {
  level: string;
  msg: string;
  ts: number;
}

// ============================================================================
// Constants (exported for testing)
// ============================================================================

export const ALLOWED_ORIGINS = ['https://chinarxiv.org'];
export const VALID_TYPES = ['translation', 'figure', 'site-bug', 'feature', 'other'] as const;
export const RATE_LIMIT = 5;  // requests per window
export const RATE_WINDOW_MS = 60 * 60 * 1000;  // 1 hour
export const API_TIMEOUT_MS = 10000;  // 10 second timeout for external APIs
export const MIN_DESCRIPTION_LENGTH = 10;
export const MAX_DESCRIPTION_LENGTH = 5000;
export const MAX_CONSOLE_LOGS = 10;
export const MAX_LOG_MESSAGE_LENGTH = 500;
export const MAX_TITLE_LENGTH = 60;

// ============================================================================
// Rate Limiting (injectable store for testing)
// ============================================================================

// Default global store (resets on worker restart)
const defaultRateLimitStore: RateLimitStore = new Map();

export function checkRateLimit(
  ip: string,
  store: RateLimitStore = defaultRateLimitStore,
  now: number = Date.now()
): boolean {
  const record = store.get(ip);

  if (!record || now > record.resetTime) {
    store.set(ip, { count: 1, resetTime: now + RATE_WINDOW_MS });
    return true;
  }

  if (record.count >= RATE_LIMIT) {
    return false;
  }

  record.count++;
  return true;
}

// ============================================================================
// Validation (pure function, exported for testing)
// ============================================================================

export function validatePayload(body: unknown): ValidationResult {
  if (!body || typeof body !== 'object') {
    return { valid: false, error: 'Invalid request body', statusCode: 400 };
  }

  const payload = body as Partial<ReportPayload>;

  // Required top-level fields
  if (!payload.type || !payload.description || !payload.context) {
    return { valid: false, error: 'Missing required fields', statusCode: 400 };
  }

  // Type whitelist
  if (!VALID_TYPES.includes(payload.type as typeof VALID_TYPES[number])) {
    return { valid: false, error: 'Invalid issue type', statusCode: 400 };
  }

  // Description length
  if (payload.description.length < MIN_DESCRIPTION_LENGTH) {
    return { valid: false, error: 'Description too short. Please provide more details.', statusCode: 400 };
  }

  if (payload.description.length > MAX_DESCRIPTION_LENGTH) {
    return { valid: false, error: `Description too long. Please keep it under ${MAX_DESCRIPTION_LENGTH} characters.`, statusCode: 400 };
  }

  // Validate required context properties (to prevent runtime errors in formatIssueBody)
  const ctx = payload.context;

  // url: required string, must be https
  if (typeof ctx.url !== 'string' || ctx.url.length === 0) {
    return { valid: false, error: 'Missing required context field: url', statusCode: 400 };
  }
  if (!ctx.url.startsWith('https://')) {
    return { valid: false, error: 'Invalid context: url must be https', statusCode: 400 };
  }

  // userAgent: required string
  if (typeof ctx.userAgent !== 'string') {
    return { valid: false, error: 'Missing required context field: userAgent', statusCode: 400 };
  }

  // consoleLogs: required array
  if (!Array.isArray(ctx.consoleLogs)) {
    return { valid: false, error: 'Missing required context field: consoleLogs', statusCode: 400 };
  }

  // timestamp: required string
  if (typeof ctx.timestamp !== 'string') {
    return { valid: false, error: 'Missing required context field: timestamp', statusCode: 400 };
  }

  // viewport: required object with numeric w and h
  if (!ctx.viewport || typeof ctx.viewport.w !== 'number' || typeof ctx.viewport.h !== 'number') {
    return { valid: false, error: 'Missing required context field: viewport', statusCode: 400 };
  }

  // referrer: required string (can be empty)
  if (typeof ctx.referrer !== 'string') {
    return { valid: false, error: 'Missing required context field: referrer', statusCode: 400 };
  }

  return { valid: true };
}

// ============================================================================
// Fetch with timeout (exported for testing)
// ============================================================================

export async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  timeoutMs: number = API_TIMEOUT_MS
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal
    });
    return response;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ============================================================================
// Markdown escaping (exported for testing)
// ============================================================================

export function escapeMarkdown(text: string): string {
  // Escape markdown special characters to prevent injection
  return text
    .replace(/\\/g, '\\\\')
    .replace(/`/g, '\\`')
    .replace(/\*/g, '\\*')
    .replace(/\_/g, '\\_')
    .replace(/\[/g, '\\[')
    .replace(/\]/g, '\\]')
    .replace(/#/g, '\\#')
    .replace(/\|/g, '\\|')
    .replace(/\r?\n/g, ' ')  // Flatten newlines to prevent table/formatting breaks
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ============================================================================
// Console log sanitization (exported for testing)
// ============================================================================

export function sanitizeConsoleLogs(
  logs: Array<{ level: string; msg: unknown[]; ts: number }>
): SanitizedLog[] {
  return logs
    .slice(-MAX_CONSOLE_LOGS)
    .map(log => ({
      level: String(log.level || '').slice(0, 10),
      msg: JSON.stringify(log.msg).slice(0, MAX_LOG_MESSAGE_LENGTH).replace(/`/g, "'"),
      ts: log.ts
    }));
}

// ============================================================================
// Issue formatting (exported for testing)
// ============================================================================

export function createIssueTitle(report: ReportPayload): string {
  const typeMap: Record<string, string> = {
    translation: 'Translation',
    figure: 'Figure',
    'site-bug': 'Bug',
    feature: 'Feature',
    other: 'Other'
  };

  const typeLabel = typeMap[report.type] || 'Report';

  // Sanitize and truncate description for title
  const sanitizedDesc = report.description
    .replace(/[\r\n]+/g, ' ')  // Remove newlines
    .replace(/[<>]/g, '')      // Remove angle brackets
    .slice(0, MAX_TITLE_LENGTH)
    .trim();

  return `[${typeLabel}] ${sanitizedDesc}${report.description.length > MAX_TITLE_LENGTH ? '...' : ''}`;
}

export function formatIssueBody(report: ReportPayload): string {
  const escapedType = escapeMarkdown(report.type);
  const escapedDesc = escapeMarkdown(report.description);
  const escapedUrl = escapeMarkdown(report.context.url);
  const escapedPaperId = report.context.paperId ? escapeMarkdown(report.context.paperId) : 'N/A';
  const escapedPaperTitle = report.context.paperTitle ? escapeMarkdown(report.context.paperTitle) : 'N/A';
  const escapedUserAgent = escapeMarkdown(report.context.userAgent);
  const escapedReferrer = report.context.referrer ? escapeMarkdown(report.context.referrer) : 'N/A';

  // Sanitize console logs
  const sanitizedLogs = sanitizeConsoleLogs(report.context.consoleLogs);
  // Escape backticks in final JSON to prevent code block breakout
  const logsJson = JSON.stringify(sanitizedLogs, null, 2).replace(/`/g, "'");

  return `## User Report

**Type:** ${escapedType}

**Description:**
${escapedDesc}

---

## Context

| Field | Value |
|-------|-------|
| URL | ${escapedUrl} |
| Paper ID | ${escapedPaperId} |
| Paper Title | ${escapedPaperTitle} |
| Timestamp | ${report.context.timestamp} |
| Browser | ${escapedUserAgent} |
| Viewport | ${report.context.viewport.w}x${report.context.viewport.h} |
| Referrer | ${escapedReferrer} |

## Console Logs (last 10)

\`\`\`json
${logsJson}
\`\`\`

---
*Submitted via Report Problem button*

---

@claude please review and triage this issue. If it is a bug or translation error, please fix. If it is a feature request, see if you think it is useful or a good idea. Ensure it isn't a security risk. If it is a good idea, propose a design but don't implement.
`;
}

// ============================================================================
// Main handler
// ============================================================================

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Check origin against allowed list
    const origin = request.headers.get('Origin') || '';
    const allowedOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];

    // CORS headers
    const corsHeaders = {
      'Access-Control-Allow-Origin': allowedOrigin,
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    // Handle preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // Verify origin is allowed
    if (origin && !ALLOWED_ORIGINS.includes(origin)) {
      return new Response(
        JSON.stringify({ success: false, error: 'Forbidden' }),
        { status: 403, headers: { 'Content-Type': 'application/json' } }
      );
    }

    // Only accept POST
    if (request.method !== 'POST') {
      return new Response(
        JSON.stringify({ success: false, error: 'Method not allowed' }),
        { status: 405, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }

    try {
      const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

      // Rate limiting
      if (!checkRateLimit(ip)) {
        return new Response(
          JSON.stringify({ success: false, error: 'Rate limit exceeded. Please try again later.' }),
          { status: 429, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      const body = await request.json();

      // Validate payload
      const validation = validatePayload(body);
      if (!validation.valid) {
        return new Response(
          JSON.stringify({ success: false, error: validation.error }),
          { status: validation.statusCode || 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      const payload = body as ReportPayload;

      // ========================================
      // DRY-RUN MODE: Skip Turnstile + GitHub
      // Used by CI smoke tests to verify deployment without side effects
      // ========================================
      const isDryRun = request.headers.get('X-Dry-Run') === 'true';
      if (isDryRun) {
        return new Response(
          JSON.stringify({
            success: true,
            dryRun: true,
            message: 'Validation passed (dry-run mode)'
          }),
          { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      // Validate Turnstile token - REQUIRED when secret is configured
      if (env.TURNSTILE_SECRET) {
        if (!payload.turnstileToken) {
          return new Response(
            JSON.stringify({ success: false, error: 'Verification required. Please try again.' }),
            { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }

        try {
          const turnstileRes = await fetchWithTimeout('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              secret: env.TURNSTILE_SECRET,
              response: payload.turnstileToken,
              remoteip: ip
            })
          });

          const turnstileData = await turnstileRes.json() as TurnstileResponse;

          if (!turnstileData.success) {
            return new Response(
              JSON.stringify({ success: false, error: 'Verification failed. Please try again.' }),
              { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
            );
          }
        } catch (error) {
          console.error('Turnstile verification error:', error);
          return new Response(
            JSON.stringify({ success: false, error: 'Verification service unavailable. Please try again.' }),
            { status: 503, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }
      }

      // Create GitHub Issue
      const issueTitle = createIssueTitle(payload);
      const issueBody = formatIssueBody(payload);

      try {
        const ghRes = await fetchWithTimeout(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'ChinaRxiv-Report-API',
            'X-GitHub-Api-Version': '2022-11-28'
          },
          body: JSON.stringify({
            title: issueTitle,
            body: issueBody,
            labels: ['user-report', 'triage-ai']
          })
        });

        if (!ghRes.ok) {
          const errorText = await ghRes.text();
          console.error('GitHub API error:', ghRes.status, errorText);
          return new Response(
            JSON.stringify({ success: false, error: 'Failed to create report. Please try again.' }),
            { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }

        const issue = await ghRes.json() as { number: number; html_url: string };

        return new Response(
          JSON.stringify({
            success: true,
            issueNumber: issue.number,
            // Don't expose the URL since it's a private repo
            message: 'Report submitted successfully. Thank you for your feedback!'
          }),
          { status: 200, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      } catch (error) {
        console.error('GitHub API error:', error);
        const isTimeout = error instanceof Error && error.name === 'AbortError';
        return new Response(
          JSON.stringify({ success: false, error: isTimeout ? 'Request timed out. Please try again.' : 'Failed to create report. Please try again.' }),
          { status: 503, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

    } catch (error) {
      console.error('Error processing report:', error);
      return new Response(
        JSON.stringify({ success: false, error: 'An error occurred. Please try again.' }),
        { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      );
    }
  }
};
