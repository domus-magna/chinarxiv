import { describe, it, expect, beforeEach } from 'vitest';
import {
  validatePayload,
  checkRateLimit,
  escapeMarkdown,
  createIssueTitle,
  formatIssueBody,
  sanitizeConsoleLogs,
  RATE_LIMIT,
  RATE_WINDOW_MS,
  MIN_DESCRIPTION_LENGTH,
  MAX_DESCRIPTION_LENGTH,
  MAX_TITLE_LENGTH,
  MAX_CONSOLE_LOGS,
  MAX_LOG_MESSAGE_LENGTH,
  VALID_TYPES,
  type RateLimitStore,
  type ReportPayload,
} from './index';

// ============================================================================
// Test Helpers
// ============================================================================

function createValidPayload(overrides: Partial<ReportPayload> = {}): ReportPayload {
  return {
    type: 'site-bug',
    description: 'This is a test bug report with enough characters',
    context: {
      url: 'https://chinarxiv.org/paper/123',
      paperId: 'chinaxiv-202401.00001',
      paperTitle: 'Test Paper Title',
      consoleLogs: [],
      userAgent: 'Mozilla/5.0 Test Browser',
      viewport: { w: 1920, h: 1080 },
      timestamp: '2025-01-01T00:00:00Z',
      referrer: 'https://google.com',
    },
    ...overrides,
  };
}

// ============================================================================
// Validation Tests
// ============================================================================

describe('validatePayload', () => {
  it('accepts valid payload', () => {
    const result = validatePayload(createValidPayload());
    expect(result.valid).toBe(true);
  });

  it('rejects null body', () => {
    const result = validatePayload(null);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('Invalid request body');
  });

  it('rejects non-object body', () => {
    const result = validatePayload('string');
    expect(result.valid).toBe(false);
  });

  it('rejects missing type', () => {
    const payload = createValidPayload();
    delete (payload as Record<string, unknown>).type;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('Missing required fields');
  });

  it('rejects missing description', () => {
    const payload = createValidPayload();
    delete (payload as Record<string, unknown>).description;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('Missing required fields');
  });

  it('rejects missing context', () => {
    const payload = createValidPayload();
    delete (payload as Record<string, unknown>).context;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('Missing required fields');
  });

  it('rejects invalid type', () => {
    const result = validatePayload(createValidPayload({ type: 'invalid-type' }));
    expect(result.valid).toBe(false);
    expect(result.error).toBe('Invalid issue type');
  });

  it('accepts all valid types', () => {
    for (const type of VALID_TYPES) {
      const result = validatePayload(createValidPayload({ type }));
      expect(result.valid).toBe(true);
    }
  });

  it('rejects description shorter than minimum', () => {
    const result = validatePayload(createValidPayload({ description: 'short' }));
    expect(result.valid).toBe(false);
    expect(result.error).toContain('too short');
  });

  it('accepts description at minimum length', () => {
    const result = validatePayload(createValidPayload({
      description: 'a'.repeat(MIN_DESCRIPTION_LENGTH)
    }));
    expect(result.valid).toBe(true);
  });

  it('rejects description longer than maximum', () => {
    const result = validatePayload(createValidPayload({
      description: 'a'.repeat(MAX_DESCRIPTION_LENGTH + 1)
    }));
    expect(result.valid).toBe(false);
    expect(result.error).toContain('too long');
  });

  it('accepts description at maximum length', () => {
    const result = validatePayload(createValidPayload({
      description: 'a'.repeat(MAX_DESCRIPTION_LENGTH)
    }));
    expect(result.valid).toBe(true);
  });

  it('rejects non-https URL', () => {
    const payload = createValidPayload();
    payload.context.url = 'http://insecure.com/page';
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('Invalid context: url must be https');
  });

  it('rejects empty URL', () => {
    const payload = createValidPayload();
    payload.context.url = '';
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('url');
  });

  it('rejects missing userAgent', () => {
    const payload = createValidPayload();
    delete (payload.context as Record<string, unknown>).userAgent;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('userAgent');
  });

  it('rejects missing consoleLogs', () => {
    const payload = createValidPayload();
    delete (payload.context as Record<string, unknown>).consoleLogs;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('consoleLogs');
  });

  it('rejects missing timestamp', () => {
    const payload = createValidPayload();
    delete (payload.context as Record<string, unknown>).timestamp;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('timestamp');
  });

  it('rejects missing viewport', () => {
    const payload = createValidPayload();
    delete (payload.context as Record<string, unknown>).viewport;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('viewport');
  });

  it('rejects invalid viewport (non-numeric)', () => {
    const payload = createValidPayload();
    (payload.context.viewport as Record<string, unknown>).w = 'wide';
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('viewport');
  });

  it('rejects missing referrer', () => {
    const payload = createValidPayload();
    delete (payload.context as Record<string, unknown>).referrer;
    const result = validatePayload(payload);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('referrer');
  });

  it('accepts https URL', () => {
    const payload = createValidPayload();
    payload.context.url = 'https://secure.com/page';
    const result = validatePayload(payload);
    expect(result.valid).toBe(true);
  });
});

// ============================================================================
// Rate Limiting Tests
// ============================================================================

describe('checkRateLimit', () => {
  let store: RateLimitStore;
  const testIp = '192.168.1.1';
  const baseTime = 1000000000000;

  beforeEach(() => {
    store = new Map();
  });

  it('allows first request from new IP', () => {
    const result = checkRateLimit(testIp, store, baseTime);
    expect(result).toBe(true);
    expect(store.get(testIp)?.count).toBe(1);
  });

  it('allows requests up to the limit', () => {
    for (let i = 0; i < RATE_LIMIT; i++) {
      const result = checkRateLimit(testIp, store, baseTime);
      expect(result).toBe(true);
    }
  });

  it('blocks requests after limit exceeded', () => {
    // Use up the limit
    for (let i = 0; i < RATE_LIMIT; i++) {
      checkRateLimit(testIp, store, baseTime);
    }
    // Next request should be blocked
    const result = checkRateLimit(testIp, store, baseTime);
    expect(result).toBe(false);
  });

  it('resets after window expires', () => {
    // Use up the limit
    for (let i = 0; i < RATE_LIMIT; i++) {
      checkRateLimit(testIp, store, baseTime);
    }
    // Should be blocked
    expect(checkRateLimit(testIp, store, baseTime)).toBe(false);

    // After window expires, should be allowed
    const afterWindow = baseTime + RATE_WINDOW_MS + 1;
    const result = checkRateLimit(testIp, store, afterWindow);
    expect(result).toBe(true);
    expect(store.get(testIp)?.count).toBe(1);
  });

  it('tracks different IPs separately', () => {
    const ip1 = '1.1.1.1';
    const ip2 = '2.2.2.2';

    // Use up limit for ip1
    for (let i = 0; i < RATE_LIMIT; i++) {
      checkRateLimit(ip1, store, baseTime);
    }

    // ip1 should be blocked, ip2 should be allowed
    expect(checkRateLimit(ip1, store, baseTime)).toBe(false);
    expect(checkRateLimit(ip2, store, baseTime)).toBe(true);
  });
});

// ============================================================================
// Markdown Escaping Tests
// ============================================================================

describe('escapeMarkdown', () => {
  it('escapes backticks', () => {
    expect(escapeMarkdown('code `here`')).toBe('code \\`here\\`');
  });

  it('escapes asterisks', () => {
    expect(escapeMarkdown('*bold*')).toBe('\\*bold\\*');
  });

  it('escapes underscores', () => {
    expect(escapeMarkdown('_italic_')).toBe('\\_italic\\_');
  });

  it('escapes square brackets', () => {
    expect(escapeMarkdown('[link](url)')).toBe('\\[link\\](url)');
  });

  it('escapes hash symbols', () => {
    expect(escapeMarkdown('# heading')).toBe('\\# heading');
  });

  it('escapes pipes (table breaking)', () => {
    expect(escapeMarkdown('col1 | col2')).toBe('col1 \\| col2');
  });

  it('flattens newlines', () => {
    expect(escapeMarkdown('line1\nline2')).toBe('line1 line2');
    expect(escapeMarkdown('line1\r\nline2')).toBe('line1 line2');
  });

  it('escapes angle brackets (HTML injection)', () => {
    expect(escapeMarkdown('<script>alert(1)</script>')).toBe('&lt;script&gt;alert(1)&lt;/script&gt;');
  });

  it('escapes backslashes', () => {
    expect(escapeMarkdown('path\\to\\file')).toBe('path\\\\to\\\\file');
  });

  it('handles complex injection attempt', () => {
    const malicious = '`code` *bold* _italic_ [link](javascript:alert(1)) # heading | table\n<script>';
    const escaped = escapeMarkdown(malicious);
    // Backticks are escaped as \` - check for unescaped backticks
    expect(escaped).not.toMatch(/(?<!\\)`/);
    expect(escaped).not.toContain('\n');
    expect(escaped).not.toContain('<');
    expect(escaped).not.toContain('>');
  });
});

// ============================================================================
// Console Log Sanitization Tests
// ============================================================================

describe('sanitizeConsoleLogs', () => {
  it('returns empty array for empty input', () => {
    expect(sanitizeConsoleLogs([])).toEqual([]);
  });

  it('limits to MAX_CONSOLE_LOGS entries', () => {
    const logs = Array.from({ length: 20 }, (_, i) => ({
      level: 'info',
      msg: [`log ${i}`],
      ts: i,
    }));
    const result = sanitizeConsoleLogs(logs);
    expect(result.length).toBe(MAX_CONSOLE_LOGS);
    // Should keep the last 10 (indices 10-19)
    expect(result[0].ts).toBe(10);
    expect(result[9].ts).toBe(19);
  });

  it('truncates level to 10 characters', () => {
    const logs = [{ level: 'verylonglevelname', msg: ['test'], ts: 1 }];
    const result = sanitizeConsoleLogs(logs);
    expect(result[0].level.length).toBe(10);
  });

  it('truncates message to MAX_LOG_MESSAGE_LENGTH', () => {
    const longMessage = 'a'.repeat(1000);
    const logs = [{ level: 'info', msg: [longMessage], ts: 1 }];
    const result = sanitizeConsoleLogs(logs);
    expect(result[0].msg.length).toBeLessThanOrEqual(MAX_LOG_MESSAGE_LENGTH);
  });

  it('replaces backticks in messages', () => {
    const logs = [{ level: 'info', msg: ['code `here`'], ts: 1 }];
    const result = sanitizeConsoleLogs(logs);
    expect(result[0].msg).not.toContain('`');
    expect(result[0].msg).toContain("'");
  });

  it('handles missing level gracefully', () => {
    const logs = [{ level: '', msg: ['test'], ts: 1 }];
    const result = sanitizeConsoleLogs(logs);
    expect(result[0].level).toBe('');
  });
});

// ============================================================================
// Issue Title Tests
// ============================================================================

describe('createIssueTitle', () => {
  it('includes type label prefix', () => {
    const payload = createValidPayload({ type: 'translation' });
    const title = createIssueTitle(payload);
    expect(title.startsWith('[Translation]')).toBe(true);
  });

  it('maps all type labels correctly', () => {
    const typeMap: Record<string, string> = {
      translation: 'Translation',
      figure: 'Figure',
      'site-bug': 'Bug',
      feature: 'Feature',
      other: 'Other',
    };

    for (const [type, label] of Object.entries(typeMap)) {
      const payload = createValidPayload({ type });
      expect(createIssueTitle(payload)).toContain(`[${label}]`);
    }
  });

  it('truncates long descriptions', () => {
    const longDesc = 'a'.repeat(100);
    const payload = createValidPayload({ description: longDesc });
    const title = createIssueTitle(payload);
    expect(title.length).toBeLessThanOrEqual(MAX_TITLE_LENGTH + '[Bug] '.length + '...'.length);
    expect(title).toContain('...');
  });

  it('removes newlines from description', () => {
    const payload = createValidPayload({ description: 'line1\nline2\r\nline3 has enough chars' });
    const title = createIssueTitle(payload);
    expect(title).not.toContain('\n');
    expect(title).not.toContain('\r');
  });

  it('removes angle brackets', () => {
    const payload = createValidPayload({ description: '<script>alert("xss")</script>' });
    const title = createIssueTitle(payload);
    expect(title).not.toContain('<');
    expect(title).not.toContain('>');
  });

  it('does not add ellipsis for short descriptions', () => {
    const payload = createValidPayload({ description: 'Short description' });
    const title = createIssueTitle(payload);
    expect(title).not.toContain('...');
  });
});

// ============================================================================
// Issue Body Tests
// ============================================================================

describe('formatIssueBody', () => {
  it('includes type in body', () => {
    const payload = createValidPayload({ type: 'feature' });
    const body = formatIssueBody(payload);
    expect(body).toContain('feature');
  });

  it('includes description', () => {
    const payload = createValidPayload({ description: 'My test description here' });
    const body = formatIssueBody(payload);
    expect(body).toContain('My test description here');
  });

  it('includes context table', () => {
    const payload = createValidPayload();
    const body = formatIssueBody(payload);
    expect(body).toContain('| Field | Value |');
    expect(body).toContain('| URL |');
    expect(body).toContain('| Paper ID |');
    expect(body).toContain('| Browser |');
    expect(body).toContain('| Viewport |');
  });

  it('includes viewport dimensions', () => {
    const payload = createValidPayload();
    payload.context.viewport = { w: 1920, h: 1080 };
    const body = formatIssueBody(payload);
    expect(body).toContain('1920x1080');
  });

  it('shows N/A for missing paperId', () => {
    const payload = createValidPayload();
    payload.context.paperId = null;
    const body = formatIssueBody(payload);
    expect(body).toMatch(/Paper ID \| N\/A/);
  });

  it('shows N/A for missing referrer', () => {
    const payload = createValidPayload();
    payload.context.referrer = '';
    const body = formatIssueBody(payload);
    expect(body).toMatch(/Referrer \| N\/A/);
  });

  it('includes console logs section', () => {
    const payload = createValidPayload();
    payload.context.consoleLogs = [
      { level: 'error', msg: ['Test error'], ts: 1234567890 }
    ];
    const body = formatIssueBody(payload);
    expect(body).toContain('Console Logs');
    expect(body).toContain('```json');
    expect(body).toContain('Test error');
  });

  it('escapes markdown in user-provided content', () => {
    const payload = createValidPayload({
      description: 'Test with [markdown](link) and `code`',
    });
    payload.context.paperId = '*bold* _italic_';
    const body = formatIssueBody(payload);
    expect(body).toContain('\\[markdown\\]');
    expect(body).toContain('\\`code\\`');
    expect(body).toContain('\\*bold\\*');
  });

  it('includes claude mention for triage', () => {
    const payload = createValidPayload();
    const body = formatIssueBody(payload);
    expect(body).toContain('@claude');
  });
});

// ============================================================================
// Integration-style Handler Tests (using SELF.fetch would go here)
// These require the worker pool, so we test the exported functions directly
// ============================================================================

describe('exported constants', () => {
  it('has reasonable rate limit', () => {
    expect(RATE_LIMIT).toBeGreaterThan(0);
    expect(RATE_LIMIT).toBeLessThanOrEqual(100);
  });

  it('has reasonable rate window', () => {
    expect(RATE_WINDOW_MS).toBeGreaterThan(60000); // At least 1 minute
    expect(RATE_WINDOW_MS).toBeLessThanOrEqual(24 * 60 * 60 * 1000); // At most 1 day
  });

  it('has reasonable description limits', () => {
    expect(MIN_DESCRIPTION_LENGTH).toBeGreaterThan(0);
    expect(MAX_DESCRIPTION_LENGTH).toBeGreaterThan(MIN_DESCRIPTION_LENGTH);
    expect(MAX_DESCRIPTION_LENGTH).toBeLessThanOrEqual(100000);
  });

  it('has valid types defined', () => {
    expect(VALID_TYPES.length).toBeGreaterThan(0);
    expect(VALID_TYPES).toContain('translation');
    expect(VALID_TYPES).toContain('site-bug');
  });
});
