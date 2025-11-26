/**
 * Backfill Orchestrator Durable Object
 *
 * Controller that orchestrates the full backfill pipeline by:
 * 1. Triggering GitHub Actions workflows for each month
 * 2. Polling workflow status until completion
 * 3. Advancing to next month automatically
 *
 * The actual work (harvest, PDF download, translation) happens in GitHub Actions
 * using the existing Python pipeline.
 */

import { DurableObject } from 'cloudflare:workers';

// Configuration constants
const WORKFLOW_FIND_DELAY_MS = 3000; // Initial delay before looking for workflow run
const WORKFLOW_FIND_MAX_ATTEMPTS = 10; // Max polling attempts to find workflow run
const WORKFLOW_FIND_POLL_INTERVAL_MS = 3000; // Delay between polling attempts
const RUN_ID_RECOVERY_MAX_ATTEMPTS = 5; // Max attempts to recover a lost runId
const MAX_ERRORS_TO_KEEP = 50; // Maximum number of errors to keep in state
const DEFAULT_POLL_INTERVAL_MS = 300000; // 5 minutes default poll interval
const WORKFLOW_MAX_DURATION_MS = 4 * 60 * 60 * 1000; // 4 hours max before timeout

interface Env {
  // DO binding
  BACKFILL_ORCHESTRATOR: DurableObjectNamespace;

  // Auth credentials
  ADMIN_USERNAME: string;
  ADMIN_PASSWORD: string;

  // GitHub API
  GITHUB_TOKEN: string;
  GITHUB_REPO: string; // e.g., "owner/repo"

  // Config
  POLL_INTERVAL_MS: string; // How often to check workflow status (default: 5 min)
  WORKFLOW_FILE: string; // e.g., "backfill.yml"
  WORKFLOW_WORKERS: string; // Parallel translation workers (default: 80)

  // Optional alerting
  ALERT_WEBHOOK_URL?: string; // Slack/Discord webhook URL for failure notifications
}

interface WorkflowRun {
  id: number;
  status: 'queued' | 'in_progress' | 'completed' | 'waiting';
  conclusion: 'success' | 'failure' | 'cancelled' | 'skipped' | 'timed_out' | null;
  html_url: string;
  created_at: string;
  updated_at: string;
}

interface BackfillState {
  // Overall status
  status: 'idle' | 'running' | 'paused' | 'completed' | 'failed';

  // Month tracking
  months: string[]; // Months to process, e.g., ["202401", "202402", ...]
  currentMonthIndex: number;

  // Current workflow run
  currentWorkflow: {
    runId: number | null;
    month: string;
    status: string;
    conclusion: string | null;
    url: string | null;
    startedAt: string | null;
    triggeredAt: string | null; // When we dispatched the workflow (for filtering runs)
    runIdRecoveryAttempts: number; // Track attempts to find lost runId
  };

  // History
  completedMonths: Array<{
    month: string;
    runId: number;
    conclusion: string;
    completedAt: string;
  }>;

  // Timing
  startedAt: string | null;
  lastActivity: string | null;

  // Errors
  errors: Array<{ message: string; timestamp: string }>;
}

const DEFAULT_STATE: BackfillState = {
  status: 'idle',
  months: [],
  currentMonthIndex: 0,
  currentWorkflow: {
    runId: null,
    month: '',
    status: '',
    conclusion: null,
    url: null,
    startedAt: null,
    triggeredAt: null,
    runIdRecoveryAttempts: 0,
  },
  completedMonths: [],
  startedAt: null,
  lastActivity: null,
  errors: [],
};

/**
 * BackfillOrchestrator Durable Object
 *
 * Acts as a controller that triggers GitHub Actions workflows
 * and monitors their progress.
 */
export class BackfillOrchestrator extends DurableObject<Env> {
  private state: BackfillState = { ...DEFAULT_STATE };

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
  }

  /**
   * Initialize state from storage, migrating old format if needed
   */
  private async loadState(): Promise<void> {
    const stored = await this.ctx.storage.get<BackfillState>('state');
    if (stored) {
      // Check if this is old state format (had different fields)
      if ('harvestPhase' in stored || 'translatePhase' in stored || !('currentWorkflow' in stored)) {
        // Old format - complete reset
        console.log('Detected old state format, resetting to default');
        this.state = { ...DEFAULT_STATE };
        await this.saveState();
      } else {
        // Merge with defaults to handle new fields gracefully
        // This ensures that if we add new fields to the state, they get default values
        this.state = {
          ...DEFAULT_STATE,
          ...stored,
          currentWorkflow: {
            ...DEFAULT_STATE.currentWorkflow,
            ...stored.currentWorkflow,
          },
        };
      }
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
   * Add error to state (keeping last MAX_ERRORS_TO_KEEP)
   */
  private addError(message: string): void {
    this.state.errors.push({
      message,
      timestamp: new Date().toISOString(),
    });
    if (this.state.errors.length > MAX_ERRORS_TO_KEEP) {
      this.state.errors = this.state.errors.slice(-MAX_ERRORS_TO_KEEP);
    }
  }

  /**
   * Send alert to webhook (Slack-compatible format)
   * No-op if ALERT_WEBHOOK_URL is not configured
   */
  private async sendAlert(emoji: string, title: string, message: string): Promise<void> {
    const webhookUrl = this.env.ALERT_WEBHOOK_URL;
    if (!webhookUrl) return;

    const payload = {
      text: `${emoji} ${title}`,
      blocks: [
        {
          type: 'section',
          text: {
            type: 'mrkdwn',
            text: `*${emoji} ${title}*\n${message}`,
          },
        },
      ],
    };

    try {
      await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      console.log(`Alert sent: ${title}`);
    } catch (error) {
      console.error('Failed to send alert webhook:', error);
    }
  }

  private async sendFailureAlert(reason: string): Promise<void> {
    await this.sendAlert(
      '🚨',
      'Backfill Failed',
      `*Reason:* ${reason}\n*Month:* ${this.state.currentWorkflow.month || 'N/A'}\n*Progress:* ${this.state.currentMonthIndex}/${this.state.months.length}`
    );
  }

  private async sendSuccessAlert(): Promise<void> {
    await this.sendAlert(
      '✅',
      'Backfill Completed',
      `*Months processed:* ${this.state.months.length}\n*Duration:* ${this.state.startedAt ? this.formatDuration(new Date(this.state.startedAt), new Date()) : 'N/A'}`
    );
  }

  private formatDuration(start: Date, end: Date): string {
    const ms = end.getTime() - start.getTime();
    const hours = Math.floor(ms / (1000 * 60 * 60));
    const minutes = Math.floor((ms % (1000 * 60 * 60)) / (1000 * 60));
    return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
  }

  /**
   * Get common GitHub API headers
   */
  private getGitHubHeaders(): HeadersInit {
    return {
      'Authorization': `Bearer ${this.env.GITHUB_TOKEN}`,
      'Accept': 'application/vnd.github.v3+json',
      'Content-Type': 'application/json',
      'User-Agent': 'ChinaXiv-Backfill-Orchestrator',
    };
  }

  // ============================================
  // GITHUB ACTIONS INTEGRATION
  // ============================================

  /**
   * Trigger a GitHub Actions workflow
   * Returns the trigger timestamp on success, null on failure
   */
  private async triggerWorkflow(month: string): Promise<Date | null> {
    const workflowFile = this.env.WORKFLOW_FILE || 'backfill.yml';
    const url = `https://api.github.com/repos/${this.env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;

    console.log(`Triggering workflow ${workflowFile} for month ${month}`);

    // Record the trigger time BEFORE the request
    const triggeredAt = new Date();

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: this.getGitHubHeaders(),
        body: JSON.stringify({
          ref: 'main',
          inputs: {
            month: month,
            workers: this.env.WORKFLOW_WORKERS || '80',
            deploy: 'true',
          },
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        console.error(`Failed to trigger workflow: ${response.status} ${error}`);
        this.addError(`Failed to trigger workflow for ${month}: ${response.status}`);
        return null;
      }

      console.log(`Workflow triggered successfully for month ${month} at ${triggeredAt.toISOString()}`);
      return triggeredAt;
    } catch (error) {
      console.error('Error triggering workflow:', error);
      this.addError(`Error triggering workflow: ${error instanceof Error ? error.message : 'Unknown'}`);
      return null;
    }
  }

  /**
   * Get workflow runs filtering by created_at > triggeredAfter
   * This ensures we find the correct run that we triggered, not some other run
   */
  private async getWorkflowRunAfterTime(triggeredAfter: Date): Promise<WorkflowRun | null> {
    const workflowFile = this.env.WORKFLOW_FILE || 'backfill.yml';
    const url = `https://api.github.com/repos/${this.env.GITHUB_REPO}/actions/workflows/${workflowFile}/runs?per_page=10`;

    try {
      const response = await fetch(url, {
        headers: this.getGitHubHeaders(),
      });

      if (!response.ok) {
        console.error(`Failed to get workflow runs: ${response.status}`);
        return null;
      }

      const data = await response.json() as { workflow_runs: WorkflowRun[] };

      if (!data.workflow_runs || data.workflow_runs.length === 0) {
        return null;
      }

      // Find the first run that was created AFTER we triggered
      // This prevents us from picking up old runs or runs from other triggers
      for (const run of data.workflow_runs) {
        const runCreatedAt = new Date(run.created_at);
        if (runCreatedAt > triggeredAfter) {
          console.log(`Found matching workflow run ${run.id} created at ${run.created_at}`);
          return run;
        }
      }

      console.log(`No workflow run found after ${triggeredAfter.toISOString()}`);
      return null;
    } catch (error) {
      console.error('Error getting workflow runs:', error);
      return null;
    }
  }

  /**
   * Poll for workflow run with exponential backoff after triggering
   * Replaces the hardcoded 5s wait with proper polling
   */
  private async findWorkflowRunAfterTrigger(triggeredAfter: Date): Promise<WorkflowRun | null> {
    console.log(`Polling for workflow run created after ${triggeredAfter.toISOString()}`);

    // Initial delay before first poll
    await new Promise(resolve => setTimeout(resolve, WORKFLOW_FIND_DELAY_MS));

    for (let attempt = 1; attempt <= WORKFLOW_FIND_MAX_ATTEMPTS; attempt++) {
      console.log(`Attempt ${attempt}/${WORKFLOW_FIND_MAX_ATTEMPTS} to find workflow run`);

      const run = await this.getWorkflowRunAfterTime(triggeredAfter);
      if (run) {
        console.log(`Found workflow run ${run.id} on attempt ${attempt}`);
        return run;
      }

      // Wait before next attempt (don't wait after last attempt)
      if (attempt < WORKFLOW_FIND_MAX_ATTEMPTS) {
        await new Promise(resolve => setTimeout(resolve, WORKFLOW_FIND_POLL_INTERVAL_MS));
      }
    }

    console.error(`Failed to find workflow run after ${WORKFLOW_FIND_MAX_ATTEMPTS} attempts`);
    this.addError(`Could not find workflow run after ${WORKFLOW_FIND_MAX_ATTEMPTS} attempts`);
    return null;
  }

  /**
   * Get status of a specific workflow run
   */
  private async getWorkflowRunStatus(runId: number): Promise<WorkflowRun | null> {
    const url = `https://api.github.com/repos/${this.env.GITHUB_REPO}/actions/runs/${runId}`;

    try {
      const response = await fetch(url, {
        headers: this.getGitHubHeaders(),
      });

      if (!response.ok) {
        console.error(`Failed to get workflow run status: ${response.status}`);
        return null;
      }

      return await response.json() as WorkflowRun;
    } catch (error) {
      console.error('Error getting workflow run status:', error);
      return null;
    }
  }

  // ============================================
  // ORCHESTRATION LOGIC
  // ============================================

  /**
   * Start processing a month - trigger the workflow
   */
  private async startMonth(): Promise<void> {
    if (this.state.currentMonthIndex >= this.state.months.length) {
      this.state.status = 'completed';
      console.log('All months completed!');
      await this.saveState();
      await this.sendSuccessAlert();
      return;
    }

    const month = this.state.months[this.state.currentMonthIndex];
    console.log(`Starting month ${month} (${this.state.currentMonthIndex + 1}/${this.state.months.length})`);

    // Trigger the workflow and get the trigger timestamp
    const triggeredAt = await this.triggerWorkflow(month);

    if (!triggeredAt) {
      this.state.status = 'failed';
      await this.saveState();
      await this.sendFailureAlert('Failed to trigger GitHub workflow');
      return;
    }

    // Poll for the workflow run with the correct created_at filter
    const run = await this.findWorkflowRunAfterTrigger(triggeredAt);

    this.state.currentWorkflow = {
      runId: run?.id || null,
      month,
      status: run?.status || 'queued',
      conclusion: run?.conclusion || null,
      url: run?.html_url || null,
      startedAt: new Date().toISOString(),
      triggeredAt: triggeredAt.toISOString(),
      runIdRecoveryAttempts: 0,
    };

    // If we couldn't find the run after polling, we'll keep trying in checkAndAdvance
    if (!run) {
      console.warn(`Could not find workflow run for ${month}, will retry in next poll`);
    }

    await this.saveState();

    // Schedule status check
    const pollInterval = parseInt(this.env.POLL_INTERVAL_MS, 10) || DEFAULT_POLL_INTERVAL_MS;
    await this.ctx.storage.setAlarm(Date.now() + pollInterval);
  }

  /**
   * Check current workflow status and advance if complete
   */
  private async checkAndAdvance(): Promise<void> {
    if (this.state.status !== 'running') {
      console.log('Not running, skipping check');
      return;
    }

    // Check for workflow timeout
    if (this.state.currentWorkflow.triggeredAt) {
      const triggeredAt = new Date(this.state.currentWorkflow.triggeredAt).getTime();
      const elapsed = Date.now() - triggeredAt;

      if (elapsed > WORKFLOW_MAX_DURATION_MS) {
        const hours = Math.round(elapsed / (60 * 60 * 1000) * 10) / 10;
        console.error(`Workflow for ${this.state.currentWorkflow.month} timed out after ${hours} hours`);
        this.state.status = 'failed';
        this.addError(`Workflow for ${this.state.currentWorkflow.month} timed out after ${hours} hours`);
        await this.saveState();
        await this.sendFailureAlert(`Workflow timed out after ${hours} hours (max: ${WORKFLOW_MAX_DURATION_MS / (60 * 60 * 1000)}h)`);
        return;
      }
    }

    const runId = this.state.currentWorkflow.runId;

    if (!runId) {
      // Try to find the run again using the triggeredAt filter
      this.state.currentWorkflow.runIdRecoveryAttempts++;

      if (this.state.currentWorkflow.runIdRecoveryAttempts > RUN_ID_RECOVERY_MAX_ATTEMPTS) {
        console.error(`Failed to find workflow run after ${RUN_ID_RECOVERY_MAX_ATTEMPTS} recovery attempts`);
        this.addError(`Could not recover runId for ${this.state.currentWorkflow.month} after ${RUN_ID_RECOVERY_MAX_ATTEMPTS} attempts`);
        this.state.status = 'failed';
        await this.saveState();
        await this.sendFailureAlert(`Could not find workflow run after ${RUN_ID_RECOVERY_MAX_ATTEMPTS} recovery attempts`);
        return;
      }

      console.log(`Attempting to recover runId (attempt ${this.state.currentWorkflow.runIdRecoveryAttempts}/${RUN_ID_RECOVERY_MAX_ATTEMPTS})`);

      // triggeredAt is required for recovery - fail if missing (corrupted state)
      if (!this.state.currentWorkflow.triggeredAt) {
        console.error('Cannot recover runId: triggeredAt is missing');
        this.addError('Cannot recover runId: triggeredAt timestamp missing from state');
        this.state.status = 'failed';
        await this.saveState();
        await this.sendFailureAlert('Cannot recover workflow run - triggeredAt missing');
        return;
      }

      const triggeredAt = new Date(this.state.currentWorkflow.triggeredAt);
      const run = await this.getWorkflowRunAfterTime(triggeredAt);
      if (run) {
        this.state.currentWorkflow.runId = run.id;
        this.state.currentWorkflow.status = run.status;
        this.state.currentWorkflow.conclusion = run.conclusion;
        this.state.currentWorkflow.url = run.html_url;
        console.log(`Recovered runId: ${run.id}`);
        // Save immediately after recovery
        await this.saveState();
      }
    } else {
      // Get current status
      const run = await this.getWorkflowRunStatus(runId);

      if (run) {
        this.state.currentWorkflow.status = run.status;
        this.state.currentWorkflow.conclusion = run.conclusion;

        console.log(`Workflow ${runId}: status=${run.status}, conclusion=${run.conclusion}`);

        if (run.status === 'completed') {
          // Record completion
          this.state.completedMonths.push({
            month: this.state.currentWorkflow.month,
            runId: runId,
            conclusion: run.conclusion || 'unknown',
            completedAt: new Date().toISOString(),
          });

          if (run.conclusion === 'success') {
            // Move to next month
            this.state.currentMonthIndex++;
            console.log(`Month ${this.state.currentWorkflow.month} completed successfully`);

            // Start next month
            await this.saveState();
            await this.startMonth();
            return;
          } else {
            // Workflow failed - stop and report
            this.state.status = 'failed';
            this.addError(`Workflow for ${this.state.currentWorkflow.month} failed: ${run.conclusion}`);
            await this.saveState();
            await this.sendFailureAlert(`Workflow failed with conclusion: ${run.conclusion}`);
            return;
          }
        }
      }
    }

    await this.saveState();

    // Schedule next check
    const pollInterval = parseInt(this.env.POLL_INTERVAL_MS, 10) || DEFAULT_POLL_INTERVAL_MS;
    await this.ctx.storage.setAlarm(Date.now() + pollInterval);
  }

  /**
   * Alarm handler - check workflow status
   */
  async alarm(): Promise<void> {
    await this.loadState();
    console.log(`Alarm fired, status: ${this.state.status}`);

    if (this.state.status === 'running') {
      await this.checkAndAdvance();
    }
  }

  // ============================================
  // HTTP HANDLERS
  // ============================================

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
      } else if (path === '/retry' && request.method === 'POST') {
        return await this.handleRetry();
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

    // Decode base64 with error handling
    let decoded: string;
    try {
      decoded = atob(encoded);
    } catch {
      return new Response('Invalid base64 encoding', { status: 400 });
    }

    // Handle passwords containing colons by only splitting on first colon
    const colonIndex = decoded.indexOf(':');
    if (colonIndex === -1) {
      return new Response('Invalid credentials format', { status: 400 });
    }
    const username = decoded.substring(0, colonIndex);
    const password = decoded.substring(colonIndex + 1);

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
    const response = {
      ...this.state,
      summary: {
        totalMonths: this.state.months.length,
        completedMonths: this.state.completedMonths.length,
        currentMonth: this.state.months[this.state.currentMonthIndex] || null,
        progress: this.state.months.length > 0
          ? `${this.state.currentMonthIndex}/${this.state.months.length}`
          : '0/0',
      },
    };

    return new Response(JSON.stringify(response, null, 2), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * Validate month string format (YYYYMM)
   */
  private isValidMonth(month: string): boolean {
    if (typeof month !== 'string' || !/^\d{6}$/.test(month)) {
      return false;
    }
    const year = parseInt(month.substring(0, 4), 10);
    const monthNum = parseInt(month.substring(4, 6), 10);
    return year >= 2000 && year <= 2100 && monthNum >= 1 && monthNum <= 12;
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

    // Parse JSON body with error handling
    let body: { months?: unknown; startYear?: unknown };
    try {
      body = await request.json();
    } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON body' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Validate and extract months
    let months: string[];
    if (body.months !== undefined) {
      // Validate months array
      if (!Array.isArray(body.months)) {
        return new Response(JSON.stringify({ error: 'months must be an array' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const invalidMonths = body.months.filter(m => !this.isValidMonth(m));
      if (invalidMonths.length > 0) {
        return new Response(JSON.stringify({
          error: `Invalid month format. Expected YYYYMM, got: ${invalidMonths.slice(0, 3).join(', ')}`,
        }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      months = body.months as string[];
    } else {
      // Validate startYear
      const startYear = body.startYear !== undefined ? body.startYear : 2024;
      if (typeof startYear !== 'number' || !Number.isInteger(startYear) || startYear < 2000 || startYear > 2100) {
        return new Response(JSON.stringify({ error: 'startYear must be an integer between 2000 and 2100' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      months = this.generateMonths(startYear);
    }

    if (months.length === 0) {
      return new Response(JSON.stringify({ error: 'No months specified' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Reset state for new run
    this.state = {
      ...DEFAULT_STATE,
      status: 'running',
      months,
      startedAt: new Date().toISOString(),
    };

    await this.saveState();

    // Start first month
    await this.startMonth();

    return new Response(JSON.stringify({
      success: true,
      message: `Backfill started for ${months.length} months`,
      months,
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /pause - Pause backfill (won't stop running workflow)
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
      message: 'Backfill paused (current workflow will continue, but next month won\'t start)',
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /resume - Resume backfill
   */
  private async handleResume(): Promise<Response> {
    if (this.state.status !== 'paused' && this.state.status !== 'failed') {
      return new Response(JSON.stringify({ error: 'Backfill not paused or failed' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    this.state.status = 'running';
    await this.saveState();

    // Check current workflow or start new one
    await this.checkAndAdvance();

    return new Response(JSON.stringify({
      success: true,
      message: 'Backfill resumed',
      state: this.state,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  /**
   * POST /retry - Retry the current failed month
   */
  private async handleRetry(): Promise<Response> {
    if (this.state.status !== 'failed') {
      return new Response(JSON.stringify({ error: 'Backfill not in failed state' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    this.state.status = 'running';
    await this.saveState();

    // Re-trigger the current month
    await this.startMonth();

    return new Response(JSON.stringify({
      success: true,
      message: `Retrying month ${this.state.currentWorkflow.month}`,
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
   * Generate list of months from startYear to current
   */
  private generateMonths(startYear: number): string[] {
    const months: string[] = [];
    const now = new Date();
    const currentYear = now.getFullYear();
    const currentMonth = now.getMonth() + 1;

    for (let year = startYear; year <= currentYear; year++) {
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

    // Forward request to DO (must use full URL for Request constructor)
    const doUrl = new URL(url.pathname + url.search, url.origin);
    const doRequest = new Request(doUrl.toString(), {
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
