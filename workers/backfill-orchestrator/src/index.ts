/**
 * Backfill Orchestrator Durable Object
 *
 * Manages long-running paper translation backfill process.
 * Uses alarms for self-waking and persistent state.
 */

import { DurableObject } from 'cloudflare:workers';
import { AwsClient } from 'aws4fetch';

interface Env {
  // DO binding
  BACKFILL_ORCHESTRATOR: DurableObjectNamespace;

  // Auth credentials
  ADMIN_USERNAME: string;
  ADMIN_PASSWORD: string;

  // B2 credentials
  B2_KEY_ID: string;
  B2_APP_KEY: string;
  B2_ENDPOINT: string;
  B2_BUCKET: string;
  B2_PREFIX: string;

  // Translation
  OPENROUTER_API_KEY: string;
  TRANSLATION_MODEL: string;

  // Config
  BATCH_SIZE: string;
  ALARM_INTERVAL_MS: string;
}

interface BackfillState {
  status: 'idle' | 'running' | 'paused' | 'completed';
  months: string[]; // Months to process, e.g., ["202401", "202402", ...]
  currentMonthIndex: number;
  currentPaperIndex: number;
  papersProcessed: number;
  papersTotal: number;
  papersTranslated: number;
  papersFlagged: number;
  papersFailed: number;
  errors: Array<{ paper_id: string; error: string; timestamp: string }>;
  startedAt: string | null;
  lastActivity: string | null;
  totalCostUsd: number;
}

interface Paper {
  id: string;
  oai_identifier: string;
  title: string;
  abstract: string;
  creators: string[];
  subjects: string[];
  date: string;
  source_url: string;
  pdf_url: string;
}

const DEFAULT_STATE: BackfillState = {
  status: 'idle',
  months: [],
  currentMonthIndex: 0,
  currentPaperIndex: 0,
  papersProcessed: 0,
  papersTotal: 0,
  papersTranslated: 0,
  papersFlagged: 0,
  papersFailed: 0,
  errors: [],
  startedAt: null,
  lastActivity: null,
  totalCostUsd: 0,
};

/**
 * BackfillOrchestrator Durable Object
 */
export class BackfillOrchestrator extends DurableObject {
  private env: Env;
  private state: BackfillState = { ...DEFAULT_STATE };
  private b2Client: AwsClient | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.env = env;
  }

  /**
   * Initialize state from storage
   */
  private async loadState(): Promise<void> {
    const stored = await this.ctx.storage.get<BackfillState>('state');
    if (stored) {
      this.state = stored;
    }
  }

  /**
   * Save state to storage
   */
  private async saveState(): Promise<void> {
    this.state.lastActivity = new Date().toISOString();
    await this.ctx.storage.put('state', this.state);
  }

  /**
   * Get B2 client (lazy init)
   */
  private getB2Client(): AwsClient {
    if (!this.b2Client) {
      this.b2Client = new AwsClient({
        accessKeyId: this.env.B2_KEY_ID,
        secretAccessKey: this.env.B2_APP_KEY,
        service: 's3',
        region: 'us-west-004',
      });
    }
    return this.b2Client;
  }

  /**
   * Fetch object from B2
   */
  private async fetchB2(key: string): Promise<string | null> {
    const client = this.getB2Client();
    const url = `${this.env.B2_ENDPOINT}/${this.env.B2_BUCKET}/${this.env.B2_PREFIX}${key}`;

    try {
      const response = await client.fetch(url);
      if (!response.ok) {
        if (response.status === 404) return null;
        throw new Error(`B2 fetch failed: ${response.status}`);
      }
      return await response.text();
    } catch (error) {
      console.error(`Error fetching ${key}:`, error);
      return null;
    }
  }

  /**
   * Upload object to B2
   */
  private async uploadB2(key: string, content: string, contentType = 'application/json'): Promise<boolean> {
    const client = this.getB2Client();
    const url = `${this.env.B2_ENDPOINT}/${this.env.B2_BUCKET}/${this.env.B2_PREFIX}${key}`;

    try {
      const response = await client.fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': contentType },
        body: content,
      });
      return response.ok;
    } catch (error) {
      console.error(`Error uploading ${key}:`, error);
      return false;
    }
  }

  /**
   * Fetch papers for a given month from harvest records
   */
  private async fetchPapersForMonth(month: string): Promise<Paper[]> {
    const key = `records/chinaxiv_${month}.json`;
    const content = await this.fetchB2(key);

    if (!content) {
      console.error(`No records found for month ${month}`);
      return [];
    }

    try {
      return JSON.parse(content) as Paper[];
    } catch {
      console.error(`Failed to parse records for month ${month}`);
      return [];
    }
  }

  /**
   * Check if paper is already translated
   */
  private async isPaperTranslated(paperId: string): Promise<boolean> {
    const key = `validated/translations/${paperId}.json`;
    const content = await this.fetchB2(key);
    return content !== null;
  }

  /**
   * Translate a paper using OpenRouter API
   */
  private async translatePaper(paper: Paper): Promise<{
    success: boolean;
    translation?: Record<string, unknown>;
    flagged?: boolean;
    error?: string;
    costUsd?: number;
  }> {
    const prompt = `Translate the following Chinese academic paper metadata to English.
Preserve all technical terms, equations, and formatting.
Return a JSON object with these fields:
- title_en: English translation of the title
- abstract_en: English translation of the abstract

Paper:
Title: ${paper.title}
Abstract: ${paper.abstract}`;

    try {
      const response = await fetch('https://openrouter.ai/api/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.env.OPENROUTER_API_KEY}`,
          'Content-Type': 'application/json',
          'HTTP-Referer': 'https://chinaxiv-english.pages.dev',
          'X-Title': 'ChinaXiv Backfill',
        },
        body: JSON.stringify({
          model: this.env.TRANSLATION_MODEL,
          messages: [
            { role: 'system', content: 'You are a professional academic translator specializing in Chinese to English translation. Always respond with valid JSON.' },
            { role: 'user', content: prompt },
          ],
          response_format: { type: 'json_object' },
          temperature: 0.3,
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        return { success: false, error: `OpenRouter API error: ${response.status} - ${error}` };
      }

      const result = await response.json() as {
        choices: Array<{ message: { content: string } }>;
        usage?: { total_tokens: number; prompt_tokens: number; completion_tokens: number };
      };

      const content = result.choices?.[0]?.message?.content;
      if (!content) {
        return { success: false, error: 'No response content from API' };
      }

      // Calculate cost (approximate for deepseek-chat)
      const usage = result.usage || { prompt_tokens: 0, completion_tokens: 0 };
      const costUsd = (usage.prompt_tokens * 0.0001 + usage.completion_tokens * 0.0002) / 1000;

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(content);
      } catch {
        return { success: false, error: 'Failed to parse translation response as JSON' };
      }

      // Simple QA check - look for Chinese characters in output
      const titleEn = String(parsed.title_en || '');
      const abstractEn = String(parsed.abstract_en || '');
      const chineseRegex = /[\u4e00-\u9fff]/;
      const flagged = chineseRegex.test(titleEn) || chineseRegex.test(abstractEn);

      const translation = {
        id: paper.id,
        oai_identifier: paper.oai_identifier,
        title_en: titleEn,
        abstract_en: abstractEn,
        body_en: [], // Would need PDF processing for full body
        creators: paper.creators,
        subjects: paper.subjects,
        date: paper.date,
        source_url: paper.source_url,
        pdf_url: paper.pdf_url,
        _translated_at: new Date().toISOString(),
        _model: this.env.TRANSLATION_MODEL,
        _qa_status: flagged ? 'flagged' : 'pass',
      };

      return { success: true, translation, flagged, costUsd };
    } catch (error) {
      return { success: false, error: `Translation error: ${error instanceof Error ? error.message : 'Unknown'}` };
    }
  }

  /**
   * Process a single paper
   */
  private async processPaper(paper: Paper): Promise<void> {
    // Skip if already translated
    if (await this.isPaperTranslated(paper.id)) {
      console.log(`Paper ${paper.id} already translated, skipping`);
      this.state.papersProcessed++;
      return;
    }

    const result = await this.translatePaper(paper);

    if (result.success && result.translation) {
      const folder = result.flagged ? 'flagged' : 'validated';
      const key = `${folder}/translations/${paper.id}.json`;

      const uploaded = await this.uploadB2(key, JSON.stringify(result.translation, null, 2));

      if (uploaded) {
        this.state.papersProcessed++;
        if (result.flagged) {
          this.state.papersFlagged++;
        } else {
          this.state.papersTranslated++;
        }
        this.state.totalCostUsd += result.costUsd || 0;
        console.log(`Translated ${paper.id} (${result.flagged ? 'flagged' : 'validated'})`);
      } else {
        this.state.papersFailed++;
        this.addError(paper.id, 'Failed to upload translation to B2');
      }
    } else {
      this.state.papersFailed++;
      this.addError(paper.id, result.error || 'Unknown error');
    }
  }

  /**
   * Add error to state (keeping last 100)
   */
  private addError(paperId: string, error: string): void {
    this.state.errors.push({
      paper_id: paperId,
      error,
      timestamp: new Date().toISOString(),
    });
    if (this.state.errors.length > 100) {
      this.state.errors = this.state.errors.slice(-100);
    }
  }

  /**
   * Process next batch of papers
   */
  private async processNextBatch(): Promise<boolean> {
    if (this.state.status !== 'running') {
      return false;
    }

    const batchSize = parseInt(this.env.BATCH_SIZE, 10) || 10;

    // Get current month
    if (this.state.currentMonthIndex >= this.state.months.length) {
      this.state.status = 'completed';
      console.log('Backfill completed!');
      return false;
    }

    const month = this.state.months[this.state.currentMonthIndex];
    const papers = await this.fetchPapersForMonth(month);

    if (papers.length === 0) {
      // Move to next month
      this.state.currentMonthIndex++;
      this.state.currentPaperIndex = 0;
      return true; // Continue with next batch
    }

    // Process batch
    const startIndex = this.state.currentPaperIndex;
    const endIndex = Math.min(startIndex + batchSize, papers.length);

    for (let i = startIndex; i < endIndex; i++) {
      if (this.state.status !== 'running') break;
      await this.processPaper(papers[i]);
    }

    // Update position
    this.state.currentPaperIndex = endIndex;

    if (this.state.currentPaperIndex >= papers.length) {
      // Month complete, move to next
      this.state.currentMonthIndex++;
      this.state.currentPaperIndex = 0;
    }

    await this.saveState();
    return this.state.status === 'running';
  }

  /**
   * Alarm handler - processes next batch and reschedules
   */
  async alarm(): Promise<void> {
    await this.loadState();

    if (this.state.status !== 'running') {
      console.log('Backfill not running, alarm cancelled');
      return;
    }

    console.log(`Alarm fired, processing batch (month ${this.state.currentMonthIndex}, paper ${this.state.currentPaperIndex})`);

    const shouldContinue = await this.processNextBatch();

    if (shouldContinue) {
      // Schedule next alarm
      const interval = parseInt(this.env.ALARM_INTERVAL_MS, 10) || 60000;
      await this.ctx.storage.setAlarm(Date.now() + interval);
    }
  }

  /**
   * HTTP handler
   */
  async fetch(request: Request): Promise<Response> {
    await this.loadState();

    const url = new URL(request.url);
    const path = url.pathname;

    // Check auth
    const authError = this.checkAuth(request);
    if (authError) return authError;

    try {
      if (path === '/status' || path === '/') {
        return this.handleStatus();
      } else if (path === '/start' && request.method === 'POST') {
        return await this.handleStart(request);
      } else if (path === '/pause' && request.method === 'POST') {
        return await this.handlePause();
      } else if (path === '/resume' && request.method === 'POST') {
        return await this.handleResume();
      } else if (path === '/reset' && request.method === 'POST') {
        return await this.handleReset();
      } else {
        return new Response(JSON.stringify({ error: 'Not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    } catch (error) {
      console.error('DO request error:', error);
      return new Response(JSON.stringify({
        error: 'Internal error',
        message: error instanceof Error ? error.message : 'Unknown',
      }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  }

  /**
   * Check basic auth
   */
  private checkAuth(request: Request): Response | null {
    const authorization = request.headers.get('Authorization');

    if (!authorization) {
      return new Response('Authentication required', {
        status: 401,
        headers: { 'WWW-Authenticate': 'Basic realm="Backfill Orchestrator"' },
      });
    }

    const [scheme, encoded] = authorization.split(' ');
    if (scheme !== 'Basic' || !encoded) {
      return new Response('Invalid auth header', { status: 400 });
    }

    const decoded = atob(encoded);
    const [username, password] = decoded.split(':');

    if (username !== this.env.ADMIN_USERNAME || password !== this.env.ADMIN_PASSWORD) {
      return new Response('Invalid credentials', {
        status: 401,
        headers: { 'WWW-Authenticate': 'Basic realm="Backfill Orchestrator"' },
      });
    }

    return null;
  }

  /**
   * GET /status - Return current state
   */
  private handleStatus(): Response {
    return new Response(JSON.stringify(this.state, null, 2), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /start - Start backfill
   */
  private async handleStart(request: Request): Promise<Response> {
    if (this.state.status === 'running') {
      return new Response(JSON.stringify({ error: 'Backfill already running' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const body = await request.json() as { months?: string[] };
    const months = body.months || this.generateMonths();

    if (months.length === 0) {
      return new Response(JSON.stringify({ error: 'No months specified' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Calculate total papers (would need to fetch each month's records to be accurate)
    // For now, estimate based on month count
    const estimatedPapersPerMonth = 500;

    this.state = {
      ...DEFAULT_STATE,
      status: 'running',
      months,
      papersTotal: months.length * estimatedPapersPerMonth,
      startedAt: new Date().toISOString(),
    };

    await this.saveState();

    // Schedule first alarm immediately
    await this.ctx.storage.setAlarm(Date.now() + 1000);

    return new Response(JSON.stringify({
      success: true,
      message: `Backfill started for ${months.length} months`,
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /pause - Pause backfill
   */
  private async handlePause(): Promise<Response> {
    if (this.state.status !== 'running') {
      return new Response(JSON.stringify({ error: 'Backfill not running' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    this.state.status = 'paused';
    await this.saveState();
    await this.ctx.storage.deleteAlarm();

    return new Response(JSON.stringify({
      success: true,
      message: 'Backfill paused',
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /resume - Resume backfill
   */
  private async handleResume(): Promise<Response> {
    if (this.state.status !== 'paused') {
      return new Response(JSON.stringify({ error: 'Backfill not paused' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    this.state.status = 'running';
    await this.saveState();

    // Schedule alarm to resume
    await this.ctx.storage.setAlarm(Date.now() + 1000);

    return new Response(JSON.stringify({
      success: true,
      message: 'Backfill resumed',
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /reset - Reset state
   */
  private async handleReset(): Promise<Response> {
    this.state = { ...DEFAULT_STATE };
    await this.saveState();
    await this.ctx.storage.deleteAlarm();

    return new Response(JSON.stringify({
      success: true,
      message: 'Backfill reset',
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * Generate list of months from 2024-01 to current
   */
  private generateMonths(): string[] {
    const months: string[] = [];
    const now = new Date();
    const currentYear = now.getFullYear();
    const currentMonth = now.getMonth() + 1;

    // Start from January 2024
    for (let year = 2024; year <= currentYear; year++) {
      const startMonth = 1;
      const endMonth = year === currentYear ? currentMonth : 12;

      for (let month = startMonth; month <= endMonth; month++) {
        const monthStr = `${year}${month.toString().padStart(2, '0')}`;
        months.push(monthStr);
      }
    }

    return months;
  }
}

/**
 * Worker entry point - routes to Durable Object
 */
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // CORS headers
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // Route to singleton Durable Object
    const id = env.BACKFILL_ORCHESTRATOR.idFromName('singleton');
    const stub = env.BACKFILL_ORCHESTRATOR.get(id);

    // Forward request to DO
    const doRequest = new Request(url.pathname + url.search, {
      method: request.method,
      headers: request.headers,
      body: request.body,
    });

    const response = await stub.fetch(doRequest);

    // Add CORS headers to response
    const newHeaders = new Headers(response.headers);
    Object.entries(corsHeaders).forEach(([key, value]) => {
      newHeaders.set(key, value);
    });

    return new Response(response.body, {
      status: response.status,
      headers: newHeaders,
    });
  },
};
