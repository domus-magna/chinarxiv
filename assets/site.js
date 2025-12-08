// Console log capture for report submissions
// Captures console.error and console.warn for debugging
const capturedLogs = [];
const _originalConsoleError = console.error;
const _originalConsoleWarn = console.warn;

console.error = function(...args) {
  capturedLogs.push({ level: 'error', msg: args, ts: Date.now() });
  if (capturedLogs.length > 50) capturedLogs.shift();  // Keep last 50
  _originalConsoleError.apply(console, args);
};

console.warn = function(...args) {
  capturedLogs.push({ level: 'warn', msg: args, ts: Date.now() });
  if (capturedLogs.length > 50) capturedLogs.shift();  // Keep last 50
  _originalConsoleWarn.apply(console, args);
};

// Global search function for tag clicks
function searchSubject(subject) {
  const input = document.getElementById('search-input');
  if (input) {
    input.value = subject;
    input.dispatchEvent(new Event('input'));
  }
}

// ============================================================================
// UNIFIED FILTERING ARCHITECTURE
// ============================================================================
// V1 (Current): Simple client-side filtering for small datasets (<5K papers)
//   - Single unified container (#articles) for all display states
//   - Filter all docs on every change
//   - Render all filtered results immediately (up to 100)
//   - Fast enough for current dataset size
//
// V2 (Future, when dataset > 5K papers):
//   1. ADD DATA NORMALIZATION:
//      - On page load, create normalized copies with lowercase fields
//      - Store as: { ...paper, lc_title, lc_abstract, lc_authors, timestamp }
//      - Prevents repeated .toLowerCase() calls during filtering
//      - Add to onIndexLoaded(): allDocs = data.map(d => ({ ...d, lc_title: d.title.toLowerCase(), ... }))
//
//   2. ADD DEBOUNCING TO SEARCH INPUT:
//      - Current: 120ms timeout (line 249) - works for small datasets
//      - V2: Increase to 200ms to reduce filtering frequency
//      - Keep category/date/figures filters instant (no debouncing)
//
//   3. ADD PROGRESSIVE RENDERING:
//      - Filter → slice(0, 200) → "Load More" button
//      - Or implement virtual scrolling for infinite scroll
//      - Example: https://github.com/virtual-list-js or similar libraries
//      - Add CSS for .load-more-btn (min-height: 44px, full width, clear styling)
//
//   4. PERFORMANCE MONITORING:
//      - Add console.time('filter') before filtering (line 150)
//      - Add console.timeEnd('filter') after filtering (line 189)
//      - If consistently > 50ms, trigger V2 optimizations
//
// Detailed V2 implementation notes available in project documentation
// ============================================================================

(() => {
  const input = document.getElementById('search-input');
  const container = document.getElementById('articles');  // V1: Single unified container
  const categoryFilter = document.getElementById('category-filter');
  const dateFilter = document.getElementById('date-filter');
  const sortOrder = document.getElementById('sort-order');
  const figuresFilter = document.getElementById('figures-filter');
  const searchBtn = document.querySelector('.search-btn');
  if (!input || !container) return;

  let miniSearch = null;
  let allDocs = [];
  let lastSearchResults = [];
  let currentQuery = '';
  let indexLoadState = 'loading'; // 'loading' | 'success' | 'failed'
  let userChangedSort = false; // Track if user explicitly changed sort
  let initialHTML = container.innerHTML; // Cache server-rendered HTML for restore

  // Date filter: days ago lookup (relative to today)
  const dateDays = { '7d': 7, '30d': 30, '90d': 90, '1y': 365 };

  // URL search parameter
  const urlQuery = new URLSearchParams(window.location.search).get('q');
  if (urlQuery) input.value = urlQuery;

  // Event delegation for subject tag clicks (prevents XSS from inline onclick)
  container.addEventListener('click', (e) => {
    const tag = e.target.closest('.subject-tag[data-subject]');
    if (tag) {
      searchSubject(tag.dataset.subject);
    }
  });

  // Initialize MiniSearch with field boosting
  function initMiniSearch(docs) {
    try {
      miniSearch = new MiniSearch({
        fields: ['title', 'authors', 'abstract', 'subjects'],
        storeFields: ['id', 'title', 'authors', 'abstract', 'subjects', 'date', 'has_figures', 'pdf_url'],
        searchOptions: { boost: { title: 3, authors: 2, subjects: 1.5, abstract: 1 }, fuzzy: 0.2, prefix: true }
      });
      miniSearch.addAll(docs);
      // V2 TODO: Add data normalization here
      // allDocs = docs.map(d => ({
      //   ...d,
      //   lc_title: (d.title || '').toLowerCase(),
      //   lc_abstract: (d.abstract || '').toLowerCase(),
      //   lc_authors: (d.authors || '').toLowerCase(),
      //   timestamp: Date.parse(d.date || '') || 0
      // }));
    } catch (e) {
      console.error('Failed to initialize search index:', e);
      showErrorMessage('Search initialization failed. Please refresh the page.');
    }
  }

  // Handle successful index load
  function onIndexLoaded(data) {
    indexLoadState = 'success';
    allDocs = data;
    initMiniSearch(data);
    // Handle any pending query or filter state
    if (currentQuery) {
      performSearch(currentQuery);
    } else if (urlQuery) {
      performSearch(urlQuery);
    } else {
      // Always re-render to handle any filter/sort changes made while loading
      applyFiltersAndRender();
    }
  }

  // Handle index load failure
  function onIndexFailed() {
    indexLoadState = 'failed';
    // V1: Show error without hiding server-rendered articles
    showErrorMessage('Failed to load search index. Showing default article list.');
  }

  // Load index (try compressed first)
  fetch('search-index.json.gz')
    .then(r => r.ok ? r.arrayBuffer().then(buf => JSON.parse(pako.inflate(new Uint8Array(buf), { to: 'string' }))) : fetch('search-index.json').then(r => r.json()))
    .then(onIndexLoaded)
    .catch(() => fetch('search-index.json').then(r => r.json()).then(onIndexLoaded).catch(onIndexFailed));

  // ============================================================================
  // APPLY FILTERS AND RENDER
  // ============================================================================
  // V1: Simple filtering logic - fast enough for <5K papers
  // V2 TODO: Add performance monitoring (console.time/timeEnd) when scaling
  // ============================================================================
  function applyFiltersAndRender() {
    // If index load failed, preserve server-rendered articles
    if (indexLoadState === 'failed') {
      return;
    }

    const cat = categoryFilter?.value || '';
    const dateRange = dateFilter?.value || '';
    const figuresOnly = figuresFilter?.checked || false;
    const hasQuery = Boolean(currentQuery);

    // Disable "Relevance" when no query (relevance requires search terms)
    const relevanceOpt = sortOrder?.querySelector('option[value="relevance"]');
    if (relevanceOpt) {
      relevanceOpt.disabled = !hasQuery;
      // If relevance was selected and query cleared, reset to newest
      if (!hasQuery && sortOrder?.value === 'relevance') {
        sortOrder.value = 'newest';
        userChangedSort = false;  // Reset so next query uses relevance ranking
      }
    }

    // Compute sortChanged AFTER any auto-reset (fixes stale state bug)
    const sortChanged = sortOrder?.value && sortOrder.value !== 'newest';

    // Any filter/sort change triggers filtering
    const hasActiveFilters = Boolean(cat || dateRange || figuresOnly || sortChanged);
    const isActive = hasQuery || hasActiveFilters;

    // V1: No active search/filters → restore server-rendered articles (already in DOM)
    // V2 TODO: When adding "Load More", may need to re-render here to reset pagination
    if (!isActive) {
      restoreServerRenderedArticles();
      return;
    }

    // Use all docs when filters are applied without a query
    const baseResults = hasQuery ? lastSearchResults : allDocs;
    if (!baseResults.length && !allDocs.length) {
      showLoadingMessage();
      return;
    }

    // V2 TODO: Add console.time('filter') here for performance monitoring
    let filtered = baseResults.filter(hit => {
      // Category filter - exact match (subjects is comma-separated string like "Physics, Nuclear Physics")
      // V2 TODO: Use pre-normalized fields for faster filtering
      if (cat) {
        const subjects = (hit.subjects || '').split(',').map(s => s.trim().toLowerCase());
        if (!subjects.includes(cat.toLowerCase())) return false;
      }
      // Date filter (reset to start of day to include papers from today)
      // V2 TODO: Use pre-computed timestamp field instead of Date.parse
      if (dateRange && dateDays[dateRange] !== undefined) {
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - dateDays[dateRange]);
        cutoff.setHours(0, 0, 0, 0);
        const hitDate = new Date(hit.date);
        if (isNaN(hitDate.getTime())) return true; // Keep papers with invalid dates
        if (hitDate < cutoff) return false;
      }
      // Figures filter
      if (figuresOnly && !hit.has_figures) return false;
      return true;
    });

    // Sort results based on user selection
    const sort = sortOrder?.value || 'newest';
    const toTimestamp = (hit) => {
      const t = Date.parse(hit.date || '');
      return Number.isNaN(t) ? 0 : t;
    };

    // With a query: preserve MiniSearch relevance ranking unless user explicitly changed sort
    // Without a query: "Relevance" falls back to "Newest First"
    if (hasQuery && !userChangedSort) {
      // Keep MiniSearch ranking order - user hasn't touched sort dropdown
    } else if (sort === 'relevance' && hasQuery) {
      // User explicitly selected "Relevance" - keep MiniSearch order
    } else if (sort === 'oldest') {
      filtered.sort((a, b) => toTimestamp(a) - toTimestamp(b) || String(a.id || '').localeCompare(String(b.id || '')));
    } else {
      // Newest first (explicit selection or fallback for no-query relevance)
      filtered.sort((a, b) => toTimestamp(b) - toTimestamp(a) || String(a.id || '').localeCompare(String(b.id || '')));
    }
    // V2 TODO: Add console.timeEnd('filter') here

    // V1: Limit to 100 results - renders instantly
    // V2 TODO: Replace with progressive rendering (slice(0, 200) + "Load More" button)
    filtered = filtered.slice(0, 100);

    renderResults(filtered, hasQuery, hasActiveFilters);
  }

  // Highlight search terms (using function replacement for safety)
  function highlightTerms(text, query) {
    if (!query || !text) return escapeHtml(text || '');
    const escaped = escapeHtml(text);
    const terms = query.toLowerCase().split(/\s+/).filter(t => t.length > 1);
    if (!terms.length) return escaped;
    const pattern = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    return escaped.replace(new RegExp(`(${pattern})`, 'gi'), (match) => '<mark>' + match + '</mark>');
  }

  // ============================================================================
  // RENDER RESULTS
  // ============================================================================
  // V1: Render .paper-card elements to match server-rendered format
  // V2 TODO: When adding "Load More", split this into renderBatch() function
  // ============================================================================
  function renderResults(hits, hasQuery, hasActiveFilters) {
    if (!hits.length) {
      // Use filter message only when dropdown filters are active
      const msg = hasActiveFilters
        ? 'No papers match with selected filters. Try adjusting them.'
        : 'No papers found. Try different keywords.';
      container.innerHTML = `<div class="no-results" style="padding: 40px; text-align: center; color: #777;">${msg}</div>`;
    } else {
      // V1: Render all filtered results as .paper-card elements
      // V2 TODO: Replace with renderBatch(hits, 0, 200) + "Load More" button
      const count = `<div class="search-results-count" style="margin-bottom: 1rem; color: #666;">Found ${hits.length} paper${hits.length > 1 ? 's' : ''}</div>`;
      container.innerHTML = count + hits.map(hit => createPaperCard(hit, hasQuery)).join('');
    }
  }

  // Create a .paper-card element matching the server-rendered HTML format
  function createPaperCard(hit, hasQuery) {
    const title = highlightTerms(hit.title || 'Untitled', currentQuery);
    const authors = escapeHtml(hit.authors || 'Unknown');
    const date = formatDate(hit.date);
    const abstract = highlightTerms((hit.abstract || '').slice(0, 300), currentQuery);
    const abstractEllipsis = (hit.abstract || '').length > 300 ? '…' : '';

    // Parse subjects (comma-separated string)
    const subjects = (hit.subjects || '').split(',').map(s => s.trim()).filter(s => s);
    const subjectTags = subjects.slice(0, 3).map(s =>
      `<span class="subject-tag" data-subject="${escapeHtml(s)}">${escapeHtml(s)}</span>`
    ).join('');

    // PDF link if available (validate protocol to prevent XSS)
    const pdfLink = hit.pdf_url && (hit.pdf_url.startsWith('http://') || hit.pdf_url.startsWith('https://'))
      ? `<a href="${escapeHtml(hit.pdf_url)}" class="btn-sm">PDF</a>`
      : '';

    return `
      <article class="paper-card">
        <h3 class="paper-title">
          <a href="/items/${hit.id}/" title="Abstract">${title}</a>
        </h3>
        <div class="paper-meta-row">
          <span class="paper-authors">${authors}</span>
        </div>
        <div class="paper-meta-row secondary">
          <span class="paper-date">${date}</span>
          <span class="paper-id">ChinaXiv:${hit.id}</span>
          ${subjectTags ? `<span class="paper-subjects">${subjectTags}</span>` : ''}
          <div class="paper-links">
            <a href="/items/${hit.id}/" class="btn-sm">Abstract</a>
            ${pdfLink}
          </div>
        </div>
        <p class="paper-abstract">${abstract}${abstractEllipsis}</p>
      </article>`;
  }

  function performSearch(query) {
    // If index load failed, preserve failure message
    if (indexLoadState === 'failed') {
      return;
    }
    currentQuery = (query || '').trim();
    if (!currentQuery) {
      lastSearchResults = [];
      applyFiltersAndRender();
      return;
    }
    if (!miniSearch) {
      showLoadingMessage();
      return;
    }
    lastSearchResults = miniSearch.search(currentQuery, { limit: 100 });
    applyFiltersAndRender();
  }

  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    if (!input.value.trim()) {
      currentQuery = '';
      lastSearchResults = [];
      applyFiltersAndRender();
      return;
    }
    timer = setTimeout(() => performSearch(input.value), 120);
  });

  // ============================================================================
  // EVENT LISTENERS
  // ============================================================================
  // V1: Instant filtering on all changes (fast enough for small datasets)
  // V2 TODO: Add debouncing to search input (increase timer from 120ms to 200ms)
  // ============================================================================

  // Subject navigation pills - sync with category dropdown
  const subjectNav = document.querySelector('.subject-nav');
  if (subjectNav) {
    subjectNav.addEventListener('click', (e) => {
      const pill = e.target.closest('.subject-nav-pill[data-subject]');
      if (!pill) return;

      // Update active state
      subjectNav.querySelectorAll('.subject-nav-pill').forEach(p => {
        p.classList.remove('active');
      });
      pill.classList.add('active');

      // Sync dropdown
      const subject = pill.dataset.subject;
      if (categoryFilter) {
        categoryFilter.value = subject;
      }

      // Filter immediately (V1 is fast enough)
      applyFiltersAndRender();
    });
  }

  // Category dropdown - sync with pills
  if (categoryFilter) {
    categoryFilter.addEventListener('change', () => {
      const subject = categoryFilter.value;

      // Sync pill highlighting
      if (subjectNav) {
        subjectNav.querySelectorAll('.subject-nav-pill').forEach(p => {
          p.classList.toggle('active', p.dataset.subject === subject);
        });
      }

      applyFiltersAndRender();
    });
  }

  if (dateFilter) dateFilter.addEventListener('change', applyFiltersAndRender);
  if (sortOrder) sortOrder.addEventListener('change', () => { userChangedSort = true; applyFiltersAndRender(); });
  if (figuresFilter) figuresFilter.addEventListener('change', applyFiltersAndRender);
  if (searchBtn) searchBtn.addEventListener('click', () => performSearch(input.value));

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Format date like Jinja's human_date filter (e.g., "Jan 15, 2024")
  function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;  // Return original if invalid
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${months[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
  }

  // Restore server-rendered articles (called when no filters/search active)
  function restoreServerRenderedArticles() {
    // V1: Restore cached server-rendered HTML to show default newest-first list
    container.innerHTML = initialHTML;
    // V2 TODO: If implementing "Load More", may need to update pagination state here
  }

  // Show loading message
  function showLoadingMessage() {
    container.innerHTML = '<div class="loading-message" style="padding: 40px; text-align: center; color: #777;">Loading search index...</div>';
  }

  // Show error message
  function showErrorMessage(msg) {
    console.error(msg);
    // Show toast notification instead of replacing container content
    showToast(msg);
  }
})();

// Header search functionality
(() => {
  const headerSearchInput = document.getElementById('header-search-input');
  const headerSearchBtn = document.getElementById('header-search-btn');

  if (!headerSearchInput || !headerSearchBtn) return;

  function performSearch(query) {
    if (!query.trim()) return;

    // Redirect to homepage with search query
    const url = new URL(window.location.origin + '/');
    url.searchParams.set('q', query.trim());
    window.location.href = url.toString();
  }

  headerSearchBtn.addEventListener('click', () => {
    performSearch(headerSearchInput.value);
  });

  headerSearchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      performSearch(headerSearchInput.value);
    }
  });
})();

// Copy to clipboard with optional custom message
function copyToClipboard(text, message) {
  const msg = message || 'Copied!';
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(msg);
    }).catch(() => {
      fallbackCopyToClipboard(text, msg);
    });
  } else {
    fallbackCopyToClipboard(text, msg);
  }
}

function fallbackCopyToClipboard(text, message) {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-999999px';
  textArea.style.top = '-999999px';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();

  try {
    document.execCommand('copy');
    showToast(message || 'Copied!');
  } catch (err) {
    console.error('Failed to copy text: ', err);
    showToast('Failed to copy');
  }

  document.body.removeChild(textArea);
}

// Generic toast notification - usable from any page
function showToast(message) {
  // Create a temporary feedback element
  const feedback = document.createElement('div');
  feedback.textContent = message;
  feedback.style.cssText = `
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: var(--primary-color, #b31b1b);
    color: white;
    padding: 12px 24px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    z-index: 1000;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    animation: toastFadeInOut 2s ease-in-out;
  `;

  // Add CSS animation if not already present
  if (!document.getElementById('toast-animation-style')) {
    const style = document.createElement('style');
    style.id = 'toast-animation-style';
    style.textContent = `
      @keyframes toastFadeInOut {
        0% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); }
        20% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
        80% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
        100% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); }
      }
    `;
    document.head.appendChild(style);
  }

  document.body.appendChild(feedback);

  // Remove feedback after animation
  setTimeout(() => {
    if (feedback.parentNode) {
      feedback.parentNode.removeChild(feedback);
    }
  }, 2000);
}

// Legacy function for backward compatibility
function showCopyFeedback() {
  showToast('Copied!');
}
