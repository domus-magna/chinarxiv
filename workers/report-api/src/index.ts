/**
 * ChinaRxiv Report API Worker
 *
 * Receives user problem reports from the website and creates GitHub issues
 * in the private chinarxiv-reports repository.
 */

interface Env {
  TURNSTILE_SECRET?: string;  // Optional for local dev
  GITHUB_TOKEN: string;
  GITHUB_REPO: string;
}

interface ReportPayload {
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

interface TurnstileResponse {
  success: boolean;
  'error-codes'?: string[];
}

// Constants
const ALLOWED_ORIGIN = 'https://chinarxiv.org';
const VALID_TYPES = ['translation', 'figure', 'site-bug', 'feature', 'other'] as const;
const RATE_LIMIT = 5;  // requests per window
const RATE_WINDOW_MS = 60 * 60 * 1000;  // 1 hour
const API_TIMEOUT_MS = 10000;  // 10 second timeout for external APIs
const MIN_DESCRIPTION_LENGTH = 10;
const MAX_DESCRIPTION_LENGTH = 5000;
const MAX_CONSOLE_LOGS = 10;
const MAX_LOG_MESSAGE_LENGTH = 500;
const MAX_TITLE_LENGTH = 60;

// Simple in-memory rate limiting (resets on worker restart)
const rateLimitMap = new Map<string, { count: number; resetTime: number }>();

function checkRateLimit(ip: string): boolean {
  const now = Date.now();
  const record = rateLimitMap.get(ip);

  if (!record || now > record.resetTime) {
    rateLimitMap.set(ip, { count: 1, resetTime: now + RATE_WINDOW_MS });
    return true;
  }

  if (record.count >= RATE_LIMIT) {
    return false;
  }

  record.count++;
  return true;
}

// Fetch with timeout wrapper
async function fetchWithTimeout(url: string, options: RequestInit, timeoutMs: number = API_TIMEOUT_MS): Promise<Response> {
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

function escapeMarkdown(text: string): string {
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

function formatIssueBody(report: ReportPayload): string {
  const escapedType = escapeMarkdown(report.type);
  const escapedDesc = escapeMarkdown(report.description);
  const escapedUrl = escapeMarkdown(report.context.url);
  const escapedPaperId = report.context.paperId ? escapeMarkdown(report.context.paperId) : 'N/A';
  const escapedPaperTitle = report.context.paperTitle ? escapeMarkdown(report.context.paperTitle) : 'N/A';
  const escapedUserAgent = escapeMarkdown(report.context.userAgent);
  const escapedReferrer = report.context.referrer ? escapeMarkdown(report.context.referrer) : 'N/A';

  // Sanitize console logs (limit size and escape)
  const sanitizedLogs = report.context.consoleLogs
    .slice(-MAX_CONSOLE_LOGS)
    .map(log => ({
      level: log.level,
      msg: JSON.stringify(log.msg).slice(0, MAX_LOG_MESSAGE_LENGTH),
      ts: log.ts
    }));

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
${JSON.stringify(sanitizedLogs, null, 2)}
\`\`\`

---
*Submitted via Report Problem button*
`;
}

function createIssueTitle(report: ReportPayload): string {
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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // CORS headers - restricted to production domain only
    const corsHeaders = {
      'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    // Handle preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // Verify origin matches (defense in depth)
    const origin = request.headers.get('Origin');
    if (origin && origin !== ALLOWED_ORIGIN) {
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

      const body = await request.json() as ReportPayload;

      // Validate required fields
      if (!body.type || !body.description || !body.context) {
        return new Response(
          JSON.stringify({ success: false, error: 'Missing required fields' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      // Validate type is in whitelist
      if (!VALID_TYPES.includes(body.type as typeof VALID_TYPES[number])) {
        return new Response(
          JSON.stringify({ success: false, error: 'Invalid issue type' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      // Validate description length
      if (body.description.length < MIN_DESCRIPTION_LENGTH) {
        return new Response(
          JSON.stringify({ success: false, error: 'Description too short. Please provide more details.' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      if (body.description.length > MAX_DESCRIPTION_LENGTH) {
        return new Response(
          JSON.stringify({ success: false, error: `Description too long. Please keep it under ${MAX_DESCRIPTION_LENGTH} characters.` }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      // Validate context URL format (basic check)
      if (body.context.url && !body.context.url.startsWith('https://')) {
        return new Response(
          JSON.stringify({ success: false, error: 'Invalid context' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
      }

      // Validate Turnstile token - REQUIRED when secret is configured
      if (env.TURNSTILE_SECRET) {
        if (!body.turnstileToken) {
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
              response: body.turnstileToken,
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
      const issueTitle = createIssueTitle(body);
      const issueBody = formatIssueBody(body);

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
