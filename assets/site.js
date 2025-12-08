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

// ============================================================================
// ADVANCED SEARCH MODAL CONTROLS
// ============================================================================
// Modal overlay and advanced filter functionality
// Integrated from recovered mockup (mockups-v3.html)
// ============================================================================

(() => {
  // Modal controls
  const modalOverlay = document.getElementById('modalOverlay');
  const advancedSearchBtn = document.getElementById('advancedSearchBtn');
  const modalClose = document.getElementById('modalClose');
  const applyFiltersBtn = document.getElementById('applyFiltersBtn');
  const clearAllBtn = document.getElementById('clearAllBtn');

  // Filter indicators
  const filterIndicators = document.getElementById('filterIndicators');
  const filterBadge = document.getElementById('filterBadge');

  // Form inputs
  const modalSearchInput = document.getElementById('modalSearchInput');
  const searchInput = document.getElementById('search-input');
  const startDate = document.getElementById('startDate');
  const endDate = document.getElementById('endDate');

  // Guard: Only run if modal elements exist
  if (!modalOverlay || !advancedSearchBtn) return;

  // Open modal
  advancedSearchBtn.addEventListener('click', () => {
    modalOverlay.classList.add('active');
    // Sync search input value from main search
    if (modalSearchInput && searchInput) {
      modalSearchInput.value = searchInput.value;
    }
  });

  // Close modal
  if (modalClose) {
    modalClose.addEventListener('click', () => {
      modalOverlay.classList.remove('active');
    });
  }

  // Close on overlay click
  modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) {
      modalOverlay.classList.remove('active');
    }
  });

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalOverlay.classList.contains('active')) {
      modalOverlay.classList.remove('active');
    }
  });

  // Apply filters
  if (applyFiltersBtn) {
    applyFiltersBtn.addEventListener('click', () => {
      let activeFilters = 0;
      const filterPillsHTML = [];

      // Sync search term from modal to main input
      if (modalSearchInput && searchInput) {
        searchInput.value = modalSearchInput.value;
        // Trigger search update
        searchInput.dispatchEvent(new Event('input'));
      }

      // Check sort order
      const selectedSort = document.querySelector('input[name="sortOrder"]:checked');
      if (selectedSort && selectedSort.value !== 'relevance') {
        const sortLabels = {
          newest: 'Newest first',
          oldest: 'Oldest first'
        };
        filterPillsHTML.push(`
          <div class="filter-indicator" data-filter-type="sort">
            Sort: ${sortLabels[selectedSort.value]} <span class="close-btn">×</span>
          </div>
        `);
        activeFilters++;
      }

      // Check date range
      if (startDate && endDate && (startDate.value || endDate.value)) {
        let dateText = '';
        if (startDate.value && endDate.value) {
          dateText = `Date: ${startDate.value} to ${endDate.value}`;
        } else if (startDate.value) {
          dateText = `After: ${startDate.value}`;
        } else {
          dateText = `Before: ${endDate.value}`;
        }
        filterPillsHTML.push(`
          <div class="filter-indicator" data-filter-type="date">
            ${dateText} <span class="close-btn">×</span>
          </div>
        `);
        activeFilters++;
      }

      // Check field-specific search
      const selectedField = document.querySelector('input[name="searchField"]:checked');
      if (selectedField && selectedField.value !== 'all') {
        const fieldLabels = {
          title: 'Title only',
          author: 'Author only',
          abstract: 'Abstract only'
        };
        filterPillsHTML.push(`
          <div class="filter-indicator" data-filter-type="field">
            ${fieldLabels[selectedField.value]} <span class="close-btn">×</span>
          </div>
        `);
        activeFilters++;
      }

      // Check "only papers with figures" filter
      const onlyFigures = document.getElementById('onlyFigures');
      if (onlyFigures && onlyFigures.checked) {
        filterPillsHTML.push(`
          <div class="filter-indicator" data-filter-type="figures">
            Papers with figures <span class="close-btn">×</span>
          </div>
        `);
        activeFilters++;
      }

      // Check category selections from accordion
      const selectedCategories = document.querySelectorAll('input[name="category"]:checked');
      if (selectedCategories.length > 0) {
        const categoryNames = Array.from(selectedCategories).map(cb => cb.value).join(', ');
        const displayText = selectedCategories.length > 3
          ? `${selectedCategories.length} categories selected`
          : categoryNames;
        filterPillsHTML.push(`
          <div class="filter-indicator" data-filter-type="categories">
            ${displayText} <span class="close-btn">×</span>
          </div>
        `);
        activeFilters++;
      }

      // Render filter pills
      if (filterIndicators) {
        if (activeFilters > 0) {
          filterIndicators.innerHTML = filterPillsHTML.join('');
          filterIndicators.style.display = 'flex';
        } else {
          filterIndicators.innerHTML = '';
          filterIndicators.style.display = 'none';
        }
      }

      // Update badge count
      if (filterBadge) {
        if (activeFilters > 0) {
          filterBadge.textContent = activeFilters;
          filterBadge.style.display = 'inline-block';
        } else {
          filterBadge.style.display = 'none';
        }
      }

      // Close modal
      modalOverlay.classList.remove('active');

      // TODO: Trigger actual filtering based on modal state
      // This will need to be wired into the MiniSearch filtering logic
    });
  }

  // Clear all filters
  if (clearAllBtn) {
    clearAllBtn.addEventListener('click', () => {
      // Reset form
      if (modalSearchInput) modalSearchInput.value = '';

      const allFields = document.getElementById('allFields');
      if (allFields) allFields.checked = true;

      const sortRelevance = document.getElementById('sortRelevance');
      if (sortRelevance) sortRelevance.checked = true;

      if (startDate) startDate.value = '';
      if (endDate) endDate.value = '';

      const onlyFigures = document.getElementById('onlyFigures');
      if (onlyFigures) onlyFigures.checked = false;

      // Clear all checkboxes in modal
      document.querySelectorAll('.modal .checkbox-group input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
      });

      // Clear category accordion checkboxes
      document.querySelectorAll('input[name="category"]:checked').forEach(cb => {
        cb.checked = false;
      });

      // Clear category filter text
      const categoryFilter = document.getElementById('categoryFilter');
      if (categoryFilter) categoryFilter.value = '';

      // Update category accordion counts
      if (window.categoryAccordion) {
        window.categoryAccordion.groups.forEach(group => {
          window.categoryAccordion.updateGroupCount(group);
        });
        window.categoryAccordion.updateSummary();
      }

      // Hide indicators
      if (filterIndicators) {
        filterIndicators.innerHTML = '';
        filterIndicators.style.display = 'none';
      }
      if (filterBadge) {
        filterBadge.style.display = 'none';
      }

      // Note: Modal stays open so user can continue editing or apply cleared state
    });
  }

  // Remove individual filter pills on close button click
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('close-btn') && e.target.closest('.filter-indicator')) {
      const pill = e.target.closest('.filter-indicator');
      const filterType = pill.dataset.filterType;

      // Clear the corresponding filter in the modal
      if (filterType === 'sort') {
        const sortRelevance = document.getElementById('sortRelevance');
        if (sortRelevance) sortRelevance.checked = true;
      } else if (filterType === 'date') {
        if (startDate) startDate.value = '';
        if (endDate) endDate.value = '';
      } else if (filterType === 'field') {
        const allFields = document.getElementById('allFields');
        if (allFields) allFields.checked = true;
      } else if (filterType === 'figures') {
        const onlyFigures = document.getElementById('onlyFigures');
        if (onlyFigures) onlyFigures.checked = false;
      } else if (filterType === 'categories') {
        document.querySelectorAll('input[name="category"]:checked').forEach(cb => {
          cb.checked = false;
        });
        if (window.categoryAccordion) {
          window.categoryAccordion.groups.forEach(group => {
            window.categoryAccordion.updateGroupCount(group);
          });
          window.categoryAccordion.updateSummary();
        }
      }

      // Remove the pill
      pill.remove();

      // Update badge count
      const remainingPills = document.querySelectorAll('.filter-indicator').length;
      if (filterBadge) {
        if (remainingPills === 0) {
          if (filterIndicators) filterIndicators.style.display = 'none';
          filterBadge.style.display = 'none';
        } else {
          filterBadge.textContent = remainingPills;
        }
      }

      // TODO: Re-apply filters to update results
    }
  });

  // Category tab toggle (for main navigation tabs)
  document.querySelectorAll('.category-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const categoryId = tab.dataset.category;

      // Update active state
      document.querySelectorAll('.category-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      // Filter papers by category group
      const articles = document.querySelectorAll('#articles .paper-card');

      if (!categoryId) {
        // Show all papers
        articles.forEach(article => article.style.display = '');
      } else {
        // Get child categories for this top-level category
        const childCategories = window.categoryTaxonomy?.[categoryId]?.children.map(c => c.name) || [];

        articles.forEach(article => {
          const subjects = article.querySelector('.paper-subjects');
          if (!subjects) {
            article.style.display = 'none';
            return;
          }

          // Check if any subject matches child categories
          const subjectTags = Array.from(subjects.querySelectorAll('.subject-tag'));
          const hasMatch = subjectTags.some(tag => {
            const subject = tag.getAttribute('data-subject');
            return childCategories.includes(subject);
          });

          article.style.display = hasMatch ? '' : 'none';
        });
      }
    });
  });
})();

// ============================================================================
// CATEGORY ACCORDION (for modal's detailed categories section)
// ============================================================================

// Debounce helper function
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

// Category accordion class
class CategoryAccordion {
  constructor(container) {
    this.container = container;
    this.filterInput = document.getElementById('categoryFilter');
    this.groups = document.querySelectorAll('.category-group');
    this.summaryCount = document.querySelector('.summary-count');
    this.clearCategoriesBtn = document.querySelector('.clear-categories');
    this.collapseAllBtn = document.querySelector('.collapse-all-categories');

    this.bindEvents();
    this.updateSummary();
  }

  bindEvents() {
    // Accordion toggle
    this.groups.forEach(group => {
      const header = group.querySelector('.category-group-header');
      if (header) {
        header.addEventListener('click', () => this.toggleGroup(group));
      }
    });

    // Search filter with debounce
    if (this.filterInput) {
      this.filterInput.addEventListener('input', debounce(() => this.filterCategories(), 150));
    }

    // Checkbox changes
    this.container.addEventListener('change', (e) => {
      if (e.target.type === 'checkbox' && e.target.name === 'category') {
        this.updateGroupCount(e.target.closest('.category-group'));
        this.updateSummary();
      }
    });

    // Clear all categories
    if (this.clearCategoriesBtn) {
      this.clearCategoriesBtn.addEventListener('click', () => this.clearAll());
    }

    // Collapse all categories
    if (this.collapseAllBtn) {
      this.collapseAllBtn.addEventListener('click', () => this.collapseAll());
    }
  }

  toggleGroup(group) {
    const header = group.querySelector('.category-group-header');
    const content = group.querySelector('.category-group-content');
    if (!header || !content) return;

    const isExpanded = header.getAttribute('aria-expanded') === 'true';
    header.setAttribute('aria-expanded', !isExpanded);
    content.hidden = isExpanded;

    // Toggle .active class for caret rotation
    if (isExpanded) {
      group.classList.remove('active');
    } else {
      group.classList.add('active');
    }
  }

  filterCategories() {
    const query = this.filterInput.value.toLowerCase().trim();

    if (!query) {
      // Show all, collapse all groups
      this.groups.forEach(group => {
        group.hidden = false;
        const content = group.querySelector('.category-group-content');
        const header = group.querySelector('.category-group-header');
        if (content) content.hidden = true;
        if (header) header.setAttribute('aria-expanded', 'false');
      });
      return;
    }

    this.groups.forEach(group => {
      const items = group.querySelectorAll('.category-item');
      let hasMatch = false;

      items.forEach(item => {
        const text = item.textContent.toLowerCase();
        const matches = text.includes(query);
        item.hidden = !matches;
        if (matches) hasMatch = true;
      });

      // Hide group if no matches, expand if has matches
      group.hidden = !hasMatch;
      if (hasMatch) {
        const content = group.querySelector('.category-group-content');
        const header = group.querySelector('.category-group-header');
        if (content) content.hidden = false;
        if (header) header.setAttribute('aria-expanded', 'true');
      }
    });
  }

  updateGroupCount(group) {
    if (!group) return;
    const checked = group.querySelectorAll('input[name="category"]:checked').length;
    const countEl = group.querySelector('.count');
    if (countEl) {
      countEl.textContent = checked ? `${checked} selected` : '0 selected';
      countEl.classList.toggle('has-selected', checked > 0);
    }
  }

  updateSummary() {
    const total = document.querySelectorAll('input[name="category"]:checked').length;
    if (this.summaryCount) {
      this.summaryCount.textContent = total > 0 ? ` - ${total} selected` : '';
    }
  }

  clearAll() {
    document.querySelectorAll('input[name="category"]:checked').forEach(cb => {
      cb.checked = false;
    });
    this.groups.forEach(group => this.updateGroupCount(group));
    this.updateSummary();
  }

  collapseAll() {
    this.groups.forEach(group => {
      const header = group.querySelector('.category-group-header');
      const content = group.querySelector('.category-group-content');
      if (header) header.setAttribute('aria-expanded', 'false');
      if (content) content.hidden = true;
    });
  }
}

/**
 * Populate category accordion from window.categoryTaxonomy (hierarchical structure)
 */
function populateCategoryAccordion() {
  const container = document.getElementById('categoryAccordion');
  if (!container || !window.categoryTaxonomy || Object.keys(window.categoryTaxonomy).length === 0) {
    console.warn('No category data available for accordion');
    return;
  }

  // Sort by order
  const sortedCategories = Object.entries(window.categoryTaxonomy)
    .sort((a, b) => a[1].order - b[1].order);

  // Build HTML for hierarchical categories
  const html = sortedCategories.map(([id, category]) => `
    <div class="category-group active">
      <div class="category-group-header">
        <span class="category-group-toggle">▼</span>
        <span class="category-group-name">${category.label} (${category.count})</span>
      </div>
      <div class="category-group-content">
        ${category.children.map(child => `
          <div class="category-item">
            <input type="checkbox" name="category" id="cat-${child.name.replace(/\s+/g, '-')}" value="${child.name}">
            <label for="cat-${child.name.replace(/\s+/g, '-')}">
              ${child.name}
              <span class="category-item-count">(${child.count})</span>
            </label>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');

  container.innerHTML = html;
}

// Populate and initialize category accordion (if modal exists on this page)
(() => {
  populateCategoryAccordion();
  const categorySection = document.querySelector('.category-section');
  if (categorySection) {
    window.categoryAccordion = new CategoryAccordion(categorySection);
  }
})();
