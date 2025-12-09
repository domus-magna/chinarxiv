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
// PHASE 1: FOUNDATION - Filter State & Helper Functions
// ============================================================================

// Global filter state (single source of truth)
const filterState = {
  query: '',              // Search term
  category: '',           // Category ID ("" = All, or "ai_computing", "physics", etc.)
  dateFrom: null,         // Date object or null
  dateTo: null,           // Date object or null
  // Future: figuresOnly, searchField, etc.
};

// Helper: Date normalization with validation (Codex fix: prevent Invalid Date bugs)
function normalizeDate(dateStr) {
  if (!dateStr) return null;
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) {
    console.warn('[Filter] Invalid date:', dateStr);
    return null;
  }
  return date;
}

// Helper: Subject normalization (Codex fix: case/whitespace consistency)
function normalizeSubject(subject) {
  // FIX: Gemini review found bug - subject?.toLowerCase().trim() throws TypeError
  // if subject is null/undefined (trim() called on undefined)
  return (subject || '').toLowerCase().trim();
}

// Helper: XSS prevention - escape HTML entities
// Note: escapeHtml() already exists at line ~423, but adding here for consistency
function escapeHTML(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[c]));
}

// State getter
function getFilterState() {
  return { ...filterState };
}

// State setter (merge updates)
// SECURITY FIX (Codex): Whitelist keys to prevent prototype pollution
function setFilterState(updates) {
  // Only allow known filter keys (prevents __proto__ injection)
  if (updates.hasOwnProperty('query') && typeof updates.query === 'string') {
    filterState.query = updates.query;
  }
  if (updates.hasOwnProperty('category') && typeof updates.category === 'string') {
    filterState.category = updates.category;
  }
  if (updates.hasOwnProperty('dateFrom')) {
    // Validate: must be Date object or null, AND must be valid (not NaN)
    if (updates.dateFrom === null ||
        (updates.dateFrom instanceof Date && !isNaN(updates.dateFrom.getTime()))) {
      filterState.dateFrom = updates.dateFrom;
    } else {
      console.warn('[Filter] Invalid dateFrom:', updates.dateFrom);
    }
  }
  if (updates.hasOwnProperty('dateTo')) {
    // Validate: must be Date object or null, AND must be valid (not NaN)
    if (updates.dateTo === null ||
        (updates.dateTo instanceof Date && !isNaN(updates.dateTo.getTime()))) {
      filterState.dateTo = updates.dateTo;
    } else {
      console.warn('[Filter] Invalid dateTo:', updates.dateTo);
    }
  }
}

// State reset
function resetFilterState() {
  filterState.query = '';
  filterState.category = '';
  filterState.dateFrom = null;
  filterState.dateTo = null;
}

// ============================================================================
// END PHASE 1
// ============================================================================

(() => {
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results'); // Legacy - may not exist
  const articleList = document.getElementById('articles');
  const categoryFilter = document.getElementById('category-filter');
  const dateFilter = document.getElementById('date-filter');
  const sortOrder = document.getElementById('sort-order');
  const figuresFilter = document.getElementById('figures-filter');
  const searchBtn = document.querySelector('.search-btn');
  if (!input || !articleList) return; // Use articleList instead of results

  let miniSearch = null;
  let allDocs = [];
  let lastSearchResults = [];
  let currentQuery = '';
  let indexLoadState = 'loading'; // 'loading' | 'success' | 'failed'
  let userChangedSort = false; // Track if user explicitly changed sort
  let currentCategory = ''; // Tracks active category tab

  // Build mapping: category_id -> [child subjects]
  // This enables hierarchical category filtering (e.g., "ai_computing" maps to ["computer science", "computer software", ...])
  const categorySubjects = {};
  if (window.categoryData) {
    for (const [categoryId, categoryDef] of Object.entries(window.categoryData)) {
      categorySubjects[categoryId] = (categoryDef.children || [])
        .map(child => typeof child === 'string' ? child.toLowerCase() : child.name.toLowerCase());
    }
  }

  // Date filter: days ago lookup (relative to today)
  const dateDays = { '7d': 7, '30d': 30, '90d': 90, '1y': 365 };

  // Event delegation for subject tag clicks (prevents XSS from inline onclick)
  // Listen on both articleList (server-rendered) and results (search-rendered)
  [articleList, results].filter(Boolean).forEach(container => {
    container.addEventListener('click', (e) => {
      const tag = e.target.closest('.subject-tag[data-subject]');
      if (tag) {
        searchSubject(tag.dataset.subject);
      }
    });
  });

  // Initialize MiniSearch with field boosting
  function initMiniSearch(docs) {
    try {
      miniSearch = new MiniSearch({
        fields: ['title', 'authors', 'abstract', 'subjects'],
        storeFields: ['id', 'title', 'authors', 'abstract', 'subjects', 'date', 'has_figures'],
        searchOptions: { boost: { title: 3, authors: 2, subjects: 1.5, abstract: 1 }, fuzzy: 0.2, prefix: true }
      });
      miniSearch.addAll(docs);
    } catch (e) {
      console.error('Failed to initialize search index:', e);
      results.innerHTML = '<div class="res"><div>Search initialization failed. Please refresh the page.</div></div>';
    }
  }

  // Handle successful index load
  function onIndexLoaded(data) {
    indexLoadState = 'success';
    allDocs = data;
    initMiniSearch(data);

    // PHASE 6: Initialize from URL (handles all filter params)
    initFromURL();
  }

  // Handle index load failure
  function onIndexFailed() {
    indexLoadState = 'failed';
    toggleArticleList(false);  // Restore article list if hidden during loading
    results.innerHTML = '<div class="res"><div>Failed to load search index.</div></div>';
  }

  // Load index (try compressed first)
  fetch('search-index.json.gz')
    .then(r => r.ok ? r.arrayBuffer().then(buf => JSON.parse(pako.inflate(new Uint8Array(buf), { to: 'string' }))) : fetch('search-index.json').then(r => r.json()))
    .then(onIndexLoaded)
    .catch(() => fetch('search-index.json').then(r => r.json()).then(onIndexLoaded).catch(onIndexFailed));

  // PHASE 5: Core filter function with security hardening
  // Apply filters and render results
  // skipPushState: prevent URL loop when calling from popstate handler
  function applyFilters({ skipPushState = false } = {}) {
    // If index load failed, preserve failure message and don't hide article list
    if (indexLoadState === 'failed') {
      return;
    }

    // Category: prioritize tab selection over dropdown
    const cat = currentCategory || categoryFilter?.value || '';
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

    // Check for absolute date filters from filterState (Phase 1)
    const hasAbsoluteDateFilter = Boolean(filterState.dateFrom || filterState.dateTo);

    // Any filter/sort change triggers browse mode (consistent behavior)
    const hasActiveFilters = Boolean(cat || dateRange || figuresOnly || sortChanged || hasAbsoluteDateFilter);
    const isActive = hasQuery || hasActiveFilters;

    // No active search/filters → show the default list
    if (!isActive) {
      toggleArticleList(false);
      results.innerHTML = '';
      updatePaperCount(allDocs.length);
      return;
    }

    // Use all docs when filters are applied without a query
    const baseResults = hasQuery ? lastSearchResults : allDocs;
    if (!baseResults.length && !allDocs.length) {
      results.innerHTML = '<div class="res"><div>Loading search index...</div></div>';
      toggleArticleList(true);
      return;
    }

    let filtered = baseResults.filter(hit => {
      // Category filter - supports both hierarchical IDs (ai_computing) and direct subject names (Computer Science)
      if (cat) {
        // If it's a hierarchical category ID, check against child subjects
        if (categorySubjects[cat]) {
          const hitSubjects = (hit.subjects || '').split(',').map(s => normalizeSubject(s));
          const hasMatch = categorySubjects[cat].some(childSubject =>
            hitSubjects.includes(normalizeSubject(childSubject))
          );
          if (!hasMatch) return false;
        } else {
          // Fallback: direct subject match (for dropdown compatibility)
          const subjects = (hit.subjects || '').split(',').map(s => normalizeSubject(s));
          if (!subjects.includes(normalizeSubject(cat))) return false;
        }
      }

      // PHASE 5: Absolute date filter (from filterState)
      if (filterState.dateFrom || filterState.dateTo) {
        const hitDate = new Date(hit.date);
        if (isNaN(hitDate.getTime())) {
          // Invalid date - keep paper to avoid hiding content
          return true;
        }
        // Set to start of day for comparison
        hitDate.setHours(0, 0, 0, 0);

        if (filterState.dateFrom) {
          const fromDate = new Date(filterState.dateFrom);
          fromDate.setHours(0, 0, 0, 0);
          if (hitDate < fromDate) return false;
        }

        if (filterState.dateTo) {
          const toDate = new Date(filterState.dateTo);
          toDate.setHours(23, 59, 59, 999); // Include entire day
          if (hitDate > toDate) return false;
        }
      }
      // Relative date filter (legacy dropdown - 7d/30d/90d/1y)
      else if (dateRange && dateDays[dateRange] !== undefined) {
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
    filtered = filtered.slice(0, 100); // Limit results

    renderResults(filtered, hasQuery, hasActiveFilters);
    updatePaperCount(filtered.length);

    // PHASE 5: Update URL with filter state (unless skipPushState)
    if (!skipPushState) {
      updateURL();
    }
  }

  // PHASE 5: Update paper count with accessibility
  function updatePaperCount(count) {
    const paperCountEl = document.getElementById('paperCount');
    if (paperCountEl) {
      const plural = count !== 1 ? 's' : '';
      paperCountEl.textContent = `Showing ${count} paper${plural}`;
      // Add aria-live for screen readers
      if (!paperCountEl.hasAttribute('aria-live')) {
        paperCountEl.setAttribute('aria-live', 'polite');
      }
    }
  }

  // PHASE 6: Initialize filter state from URL parameters
  // Parses URL and updates filterState, currentQuery, currentCategory, and UI
  function initFromURL({ skipPushState = false } = {}) {
    const params = new URLSearchParams(window.location.search);

    // CODEX FIX: Always set query (clear if absent from URL)
    const query = params.get('q') || '';
    if (input) input.value = query;
    currentQuery = query;
    setFilterState({ query });

    // Parse category parameter
    const category = params.get('category') || '';
    setFilterState({ category });
    currentCategory = category;

    // Parse date range parameters
    const dateFrom = normalizeDate(params.get('from'));
    const dateTo = normalizeDate(params.get('to'));
    setFilterState({ dateFrom, dateTo });

    // Update category tab UI
    const categoryTabs = document.querySelectorAll('.category-tab');
    categoryTabs.forEach(tab => {
      if (tab.dataset.category === category) {
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
      } else {
        tab.classList.remove('active');
        tab.setAttribute('aria-selected', 'false');
      }
    });

    // CODEX FIX: Always pass skipPushState through to prevent popstate loops
    if (query) {
      // Perform search but skip URL push in popstate context
      if (skipPushState) {
        // Manually trigger search without URL update
        if (!miniSearch) {
          // Index not ready yet - will be handled by onIndexLoaded
          return;
        }
        lastSearchResults = miniSearch.search(query, { limit: 100 });
        applyFilters({ skipPushState: true });
      } else {
        performSearch(query);
      }
    } else {
      applyFilters({ skipPushState });
    }
  }

  // PHASE 5/6: Centralized URL state management
  // PHASE 6 FIX: Gate pushState to prevent history spam (only push if state changed)
  function updateURL() {
    const url = new URL(window.location);

    // Build new URL params from filterState
    const newParams = new URLSearchParams();

    if (filterState.query) {
      newParams.set('q', filterState.query);
    }

    if (filterState.category) {
      newParams.set('category', filterState.category);
    }

    if (filterState.dateFrom) {
      newParams.set('from', filterState.dateFrom.toISOString().split('T')[0]);
    }

    if (filterState.dateTo) {
      newParams.set('to', filterState.dateTo.toISOString().split('T')[0]);
    }

    // PHASE 6 FIX: Only push if URL actually changed (prevents history spam)
    const newSearch = newParams.toString();
    const currentSearch = window.location.search.slice(1); // Remove leading '?'

    if (newSearch !== currentSearch) {
      url.search = newSearch;
      window.history.pushState({}, '', url);
    }
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

  // PHASE 5: Render results using DOM API (XSS prevention)
  // Security: Use createElement/textContent instead of innerHTML with template literals
  function renderResults(hits, hasQuery, hasActiveFilters) {
    toggleArticleList(hasQuery || hasActiveFilters);

    // Clear existing results
    results.innerHTML = '';

    if (!hits.length) {
      // Empty state
      const emptyDiv = document.createElement('div');
      emptyDiv.className = 'res';
      const msg = document.createElement('div');
      msg.textContent = hasActiveFilters
        ? 'No papers match with selected filters. Try adjusting them.'
        : 'No papers found. Try different keywords.';
      emptyDiv.appendChild(msg);
      results.appendChild(emptyDiv);
      return;
    }

    // Results count header
    const countDiv = document.createElement('div');
    countDiv.className = 'search-results-count';
    countDiv.textContent = `Found ${hits.length} paper${hits.length > 1 ? 's' : ''}`;
    results.appendChild(countDiv);

    // Render each result using DOM API
    hits.forEach(hit => {
      const resDiv = document.createElement('div');
      resDiv.className = 'res';

      // Title with link
      const titleDiv = document.createElement('div');
      titleDiv.className = 'res-title';
      const link = document.createElement('a');
      // PHASE 6 FIX: Use encodeURIComponent for URL paths (not escapeHtml)
      link.href = `/items/${encodeURIComponent(hit.id)}/`;
      // Use innerHTML only for highlighted terms (already escaped by highlightTerms)
      link.innerHTML = highlightTerms(hit.title || '', currentQuery);
      titleDiv.appendChild(link);
      resDiv.appendChild(titleDiv);

      // Metadata (date and authors)
      const metaDiv = document.createElement('div');
      metaDiv.className = 'res-meta';
      metaDiv.textContent = `${hit.date || ''} — ${hit.authors || ''}`;
      resDiv.appendChild(metaDiv);

      // Abstract (truncated)
      const abstractDiv = document.createElement('div');
      abstractDiv.className = 'res-abstract';
      const abstractText = (hit.abstract || '').slice(0, 280);
      // Use innerHTML only for highlighted terms (already escaped by highlightTerms)
      abstractDiv.innerHTML = highlightTerms(abstractText, currentQuery) + '…';
      resDiv.appendChild(abstractDiv);

      results.appendChild(resDiv);
    });
  }

  function performSearch(query) {
    // If index load failed, preserve failure message
    if (indexLoadState === 'failed') {
      return;
    }
    currentQuery = (query || '').trim();

    // PHASE 6 FIX: Sync query to filterState so updateURL() preserves it
    setFilterState({ query: currentQuery });

    if (!currentQuery) {
      lastSearchResults = [];
      applyFilters();
      return;
    }
    if (!miniSearch) { results.innerHTML = '<div class="res search-loading"><div>Loading search index...</div></div>'; return; }
    lastSearchResults = miniSearch.search(currentQuery, { limit: 100 });
    applyFilters();
  }

  // PHASE 3: Search Box - integrate with filterState
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    if (!input.value.trim()) {
      setFilterState({ query: '' }); // PHASE 3: Clear query in state
      currentQuery = ''; // Keep for backward compat
      lastSearchResults = [];
      applyFilters();
      return;
    }
    timer = setTimeout(() => {
      setFilterState({ query: input.value }); // PHASE 3: Store query in state
      performSearch(input.value);
    }, 120);
  });

  if (categoryFilter) categoryFilter.addEventListener('change', applyFilters);
  if (dateFilter) dateFilter.addEventListener('change', applyFilters);
  if (sortOrder) sortOrder.addEventListener('change', () => { userChangedSort = true; applyFilters(); });
  if (figuresFilter) figuresFilter.addEventListener('change', applyFilters);
  if (searchBtn) searchBtn.addEventListener('click', () => performSearch(input.value));

  // Category tab click handlers
  // URL state strategy:
  // - Category: ?category=ai_computing
  // - Future: ?category=physics&date=30d&figures=true
  // - Uses URLSearchParams for safe query string handling
  // - Shareable links restore filter state on page load
  const categoryTabs = document.querySelectorAll('.category-tab');
  categoryTabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      e.preventDefault();

      // Update active tab styling
      categoryTabs.forEach(t => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
      });
      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');

      // Set current category and trigger filter
      const category = tab.dataset.category || '';
      setFilterState({ category }); // PHASE 2: Use filter state
      currentCategory = category; // Keep for backward compat

      // CODEX FIX: Clear search in filterState too (not just UI)
      currentQuery = '';
      lastSearchResults = [];
      if (input) input.value = '';
      setFilterState({ query: '' }); // Clear query from state so URL updates correctly

      applyFilters(); // PHASE 5: URL update now handled inside applyFilters()
    });
  });

  // PHASE 6: Handle browser back/forward buttons (restore all filter state from URL)
  window.addEventListener('popstate', () => {
    initFromURL({ skipPushState: true }); // Restore all filters, prevent URL loop
  });

  // TODO: Advanced filters integration
  // The advanced filters modal (src/templates/index.html:113-233) contains:
  // - Field-specific search (title/author/abstract)
  // - Sort order options (relevance/newest/oldest)
  // - Date range filters (7d/30d/90d/1y)
  // - Category accordion (hierarchical category tree)
  // - Figures-only filter
  //
  // Integration approach:
  // 1. Wire up modal open/close handlers (advancedSearchBtn, modalClose)
  // 2. Sync modal state with current filters (currentCategory, dateFilter, etc.)
  // 3. Apply modal filters via applyFiltersAndRender()
  // 4. Update filter indicator pills (filterIndicators div)
  // See TODO.md for detailed implementation plan

  // Advanced Search Modal
  const advancedSearchBtn = document.getElementById('advancedSearchBtn');
  const modalOverlay = document.getElementById('modalOverlay');
  const modalClose = document.getElementById('modalClose');

  if (advancedSearchBtn && modalOverlay && modalClose) {
    // Open modal
    advancedSearchBtn.addEventListener('click', () => {
      modalOverlay.style.display = 'flex';
    });

    // Close modal
    modalClose.addEventListener('click', () => {
      modalOverlay.style.display = 'none';
    });

    // Close on overlay click
    modalOverlay.addEventListener('click', (e) => {
      if (e.target === modalOverlay) {
        modalOverlay.style.display = 'none';
      }
    });

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modalOverlay.style.display === 'flex') {
        modalOverlay.style.display = 'none';
      }
    });

    // PHASE 4: Wire up Apply and Clear buttons
    const applyFiltersBtn = document.getElementById('applyFiltersBtn');
    const clearAllBtn = document.getElementById('clearAllBtn');

    if (applyFiltersBtn) {
      applyFiltersBtn.addEventListener('click', () => {
        // Read category from modal radio buttons
        const selectedCategory = document.querySelector('input[name="category"]:checked');
        const category = selectedCategory ? selectedCategory.value : '';

        // Read date range (if date inputs exist in modal)
        const dateFromInput = document.getElementById('dateFrom');
        const dateToInput = document.getElementById('dateTo');
        const dateFrom = dateFromInput ? normalizeDate(dateFromInput.value) : null;
        const dateTo = dateToInput ? normalizeDate(dateToInput.value) : null;

        // Update filter state (Phase 1 foundation)
        setFilterState({ category, dateFrom, dateTo });

        // Sync legacy variables
        currentCategory = category;

        // Update tab highlighting to match modal selection
        categoryTabs.forEach(tab => {
          if (tab.dataset.category === category) {
            tab.classList.add('active');
            tab.setAttribute('aria-selected', 'true');
          } else {
            tab.classList.remove('active');
            tab.setAttribute('aria-selected', 'false');
          }
        });

        // PHASE 5: Apply filters and close modal (URL update handled inside applyFilters)
        applyFilters();
        modalOverlay.style.display = 'none';
      });
    }

    if (clearAllBtn) {
      clearAllBtn.addEventListener('click', () => {
        // Reset filter state (Phase 1 foundation)
        resetFilterState();

        // Sync legacy variables
        currentCategory = '';
        currentQuery = '';
        lastSearchResults = [];

        // Clear search input
        if (input) input.value = '';

        // Update tab highlighting (select "All Recent")
        categoryTabs.forEach(tab => {
          if (tab.dataset.category === '') {
            tab.classList.add('active');
            tab.setAttribute('aria-selected', 'true');
          } else {
            tab.classList.remove('active');
            tab.setAttribute('aria-selected', 'false');
          }
        });

        // PHASE 5: Apply filters and close modal (URL update handled inside applyFilters)
        applyFilters();
        modalOverlay.style.display = 'none';
      });
    }
  }

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function toggleArticleList(hide) {
    if (!articleList) return;
    articleList.style.display = hide ? 'none' : '';
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
