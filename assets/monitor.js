/**
 * ChinaXiv Pipeline Monitor Dashboard
 *
 * Fetches pipeline status from B2 manifest files and updates the dashboard.
 * Uses cache-busting query params to avoid stale data.
 *
 * Configuration is injected via MANIFEST_CONFIG global from the template.
 */

// Default config - overridden by Jinja template
if (typeof MANIFEST_CONFIG === "undefined") {
  var MANIFEST_CONFIG = {
    base: "https://f004.backblazeb2.com/file/chinaxiv",
    repoPublic: true,
  };
}

// State
let lastKnownStatus = null;
let autoRefreshInterval = null;

/**
 * Format timestamp for display.
 */
function formatTime(isoString) {
  if (!isoString) return "-";
  try {
    return new Date(isoString).toLocaleString();
  } catch (e) {
    return isoString;
  }
}

/**
 * Format relative time (e.g., "5 minutes ago").
 */
function formatRelativeTime(isoString) {
  if (!isoString) return "-";
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);

    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins} minute${diffMins > 1 ? "s" : ""} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? "s" : ""} ago`;
    return formatTime(isoString);
  } catch (e) {
    return "-";
  }
}

/**
 * Get status badge class based on status string.
 */
function getStatusClass(status) {
  switch (status) {
    case "completed":
      return "status online";
    case "in_progress":
      return "status online";
    case "failed":
      return "status offline";
    default:
      return "status unknown";
  }
}

/**
 * Get human-readable status text.
 */
function getStatusText(status) {
  switch (status) {
    case "completed":
      return "Completed";
    case "in_progress":
      return "Running";
    case "failed":
      return "Failed";
    default:
      return "Unknown";
  }
}

/**
 * Fetch JSON from B2 with cache-busting.
 */
async function fetchManifest(path) {
  const timestamp = Date.now();
  const url = `${MANIFEST_CONFIG.base}/${path}?t=${timestamp}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Update the dashboard with status and inventory data.
 */
function updateDashboard(status, inventory) {
  // Stage and status
  const stageText = status.stage
    ? `${status.stage.charAt(0).toUpperCase()}${status.stage.slice(1)}`
    : "Idle";
  const monthText = status.month ? ` (${status.month})` : "";

  // Update job stats
  const counts = status.counts || { total: 0, completed: 0, failed: 0 };
  const pending = counts.total - counts.completed - counts.failed;
  const progressPercent =
    counts.total > 0 ? (counts.completed / counts.total) * 100 : 0;

  document.getElementById("totalJobs").textContent = counts.total;
  document.getElementById("completedJobs").textContent = counts.completed;
  document.getElementById("pendingJobs").textContent = pending;
  document.getElementById("failedJobs").textContent = counts.failed;
  document.getElementById("progressPercent").textContent =
    progressPercent.toFixed(1) + "%";
  document.getElementById("progressBar").style.width = progressPercent + "%";

  // Progress bar color based on status
  const progressBar = document.getElementById("progressBar");
  if (status.status === "failed") {
    progressBar.style.background = "#e74c3c";
  } else if (status.status === "completed") {
    progressBar.style.background = "#27ae60";
  } else {
    progressBar.style.background = "#3498db";
  }

  // Estimated completion (rough estimate based on rate)
  let estimatedCompletion = "-";
  if (
    status.status === "in_progress" &&
    counts.completed > 0 &&
    pending > 0 &&
    status.started_at
  ) {
    try {
      const startTime = new Date(status.started_at);
      const elapsed = Date.now() - startTime;
      const rate = counts.completed / elapsed; // papers per ms
      const remaining = pending / rate;
      const eta = new Date(Date.now() + remaining);
      estimatedCompletion = formatTime(eta.toISOString());
    } catch (e) {
      estimatedCompletion = "-";
    }
  }
  document.getElementById("estimatedCompletion").textContent =
    estimatedCompletion;

  // System stats
  document.getElementById("uptime").textContent = `${stageText}${monthText}`;
  document.getElementById("lastUpdate").textContent = formatRelativeTime(
    status.updated_at
  );
  document.getElementById("siteUrl").textContent = "https://chinarxiv.org";

  // GitHub Actions status
  const githubStatus = document.getElementById("githubStatus");
  githubStatus.textContent = getStatusText(status.status);
  githubStatus.className = getStatusClass(status.status);

  // GitHub run link
  const runLink = document.getElementById("runLink");
  if (runLink) {
    if (MANIFEST_CONFIG.repoPublic && status.run_url) {
      runLink.href = status.run_url;
      runLink.style.display = "inline";
    } else {
      runLink.style.display = "none";
    }
  }

  // Cloudflare status (always online if we got here)
  const cloudflareStatus = document.getElementById("cloudflareStatus");
  cloudflareStatus.textContent = "Online";
  cloudflareStatus.className = "status online";

  // Update logs with inventory summary
  const logsContainer = document.getElementById("logs");
  logsContainer.innerHTML = "";

  const logs = [];

  // Current stage log
  if (status.stage) {
    logs.push({
      timestamp: status.updated_at,
      message: `Stage: ${stageText}${monthText} - ${getStatusText(
        status.status
      )} (${counts.completed}/${counts.total})`,
    });
  }

  // Inventory summary
  if (inventory) {
    logs.push({
      timestamp: inventory.updated_at,
      message: `Inventory: ${inventory.validated || 0} translations, ${
        inventory.pdfs || 0
      } PDFs, ${inventory.figures || 0} figures`,
    });

    // Per-month stats if available
    const months = Object.keys(inventory.by_month || {}).sort().reverse();
    for (const month of months.slice(0, 3)) {
      const m = inventory.by_month[month];
      logs.push({
        timestamp: inventory.updated_at,
        message: `  ${month}: ${m.validated || 0} validated, ${
          m.figures || 0
        } figures`,
      });
    }
  }

  // Error message if present
  if (status.error) {
    logs.push({
      timestamp: status.updated_at,
      message: `Error: ${status.error}`,
    });
  }

  logs.forEach((log) => {
    const logEntry = document.createElement("div");
    logEntry.className = "log-entry";
    logEntry.innerHTML = `
      <div class="log-timestamp">${formatRelativeTime(log.timestamp)}</div>
      <div>${log.message}</div>
    `;
    logsContainer.appendChild(logEntry);
  });
}

/**
 * Show stale data warning.
 */
function showStaleWarning(timestamp) {
  const warning = document.getElementById("status-warning");
  if (!warning) return;

  if (timestamp) {
    warning.textContent = `Unable to refresh. Last update: ${formatTime(
      timestamp
    )}`;
  } else {
    warning.textContent = `Unable to load status`;
  }
  warning.style.display = "block";
}

/**
 * Hide the stale warning.
 */
function hideError() {
  const warning = document.getElementById("status-warning");
  if (warning) {
    warning.style.display = "none";
  }
}

/**
 * Main refresh function.
 */
async function refreshData() {
  try {
    const [status, inventory] = await Promise.all([
      fetchManifest("status/pipeline-status.json"),
      fetchManifest("status/inventory.json").catch(() => null),
    ]);

    // Only update if newer than last known status (guards against late failure writes)
    if (
      !lastKnownStatus ||
      !status.updated_at ||
      !lastKnownStatus.updated_at ||
      new Date(status.updated_at) >= new Date(lastKnownStatus.updated_at)
    ) {
      updateDashboard(status, inventory);
      lastKnownStatus = status;
    }

    hideError();
  } catch (e) {
    console.error("Failed to fetch status:", e);
    showStaleWarning(lastKnownStatus?.updated_at);

    // If we have cached status, keep showing it
    if (lastKnownStatus) {
      updateDashboard(lastKnownStatus, null);
    }
  }
}

/**
 * Toggle auto-refresh.
 */
function toggleAutoRefresh() {
  const checkbox = document.getElementById("autoRefresh");
  if (checkbox.checked) {
    autoRefreshInterval = setInterval(refreshData, 30000); // 30 seconds
  } else {
    if (autoRefreshInterval) {
      clearInterval(autoRefreshInterval);
      autoRefreshInterval = null;
    }
  }
}

// Initial load
document.addEventListener("DOMContentLoaded", function () {
  refreshData();
});
