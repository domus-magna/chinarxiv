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

(() => {
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  const articleList = document.getElementById('articles');
  const categoryFilter = document.getElementById('category-filter');
  const dateFilter = document.getElementById('date-filter');
  const sortOrder = document.getElementById('sort-order');
  const figuresFilter = document.getElementById('figures-filter');
  const searchBtn = document.querySelector('.search-btn');
  if (!input || !results) return;

  let miniSearch = null;
  let allDocs = [];
  let lastSearchResults = [];
  let currentQuery = '';
  let indexLoadState = 'loading'; // 'loading' | 'success' | 'failed'
  let userChangedSort = false; // Track if user explicitly changed sort

  // Date filter: days ago lookup (relative to today)
  const dateDays = { '7d': 7, '30d': 30, '90d': 90, '1y': 365 };

  // URL search parameter
  const urlQuery = new URLSearchParams(window.location.search).get('q');
  if (urlQuery) input.value = urlQuery;

  // Event delegation for subject tag clicks (prevents XSS from inline onclick)
  results.addEventListener('click', (e) => {
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
    toggleArticleList(false);  // Restore article list if hidden during loading
    results.innerHTML = '<div class="res"><div>Failed to load search index.</div></div>';
  }

  // Load index (try compressed first)
  fetch('search-index.json.gz')
    .then(r => r.ok ? r.arrayBuffer().then(buf => JSON.parse(pako.inflate(new Uint8Array(buf), { to: 'string' }))) : fetch('search-index.json').then(r => r.json()))
    .then(onIndexLoaded)
    .catch(() => fetch('search-index.json').then(r => r.json()).then(onIndexLoaded).catch(onIndexFailed));

  // Apply filters and render
  function applyFiltersAndRender() {
    // If index load failed, preserve failure message and don't hide article list
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

    // Any filter/sort change triggers browse mode (consistent behavior)
    const hasActiveFilters = Boolean(cat || dateRange || figuresOnly || sortChanged);
    const isActive = hasQuery || hasActiveFilters;

    // No active search/filters → show the default list
    if (!isActive) {
      toggleArticleList(false);
      results.innerHTML = '';
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
      // Category filter - exact match (subjects is comma-separated string like "Physics, Nuclear Physics")
      if (cat) {
        const subjects = (hit.subjects || '').split(',').map(s => s.trim().toLowerCase());
        if (!subjects.includes(cat.toLowerCase())) return false;
      }
      // Date filter (reset to start of day to include papers from today)
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
    filtered = filtered.slice(0, 100); // Limit results

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

  // Render results - uses same structure as .paper-card for unified styling
  function renderResults(hits, hasQuery, hasActiveFilters) {
    toggleArticleList(hasQuery || hasActiveFilters);
    if (!hits.length) {
      // Use filter message only when dropdown filters are active
      const msg = hasActiveFilters
        ? 'No papers match with selected filters. Try adjusting them.'
        : 'No papers found. Try different keywords.';
      results.innerHTML = `<article class="paper-card"><p>${msg}</p></article>`;
    } else {
      const count = `<div class="search-results-count">Found ${hits.length} paper${hits.length > 1 ? 's' : ''}</div>`;
      results.innerHTML = count + hits.map(hit => {
        // Parse subjects (comma-separated string)
        const subjects = (hit.subjects || '').split(',').map(s => s.trim()).filter(Boolean).slice(0, 3);
        const subjectTags = subjects.map(s =>
          `<span class="subject-tag" data-subject="${escapeHtml(s)}">${escapeHtml(s)}</span>`
        ).join('');

        return `
        <article class="paper-card">
          <h3 class="paper-title">
            <a href="/items/${hit.id}/" title="Abstract">${highlightTerms(hit.title || '', currentQuery)}</a>
          </h3>
          <div class="paper-meta-row">
            <span class="paper-authors">${escapeHtml(hit.authors || 'Unknown')}</span>
          </div>
          <div class="paper-meta-row secondary">
            <span class="paper-date">${escapeHtml(hit.date || '')}</span>
            <span class="paper-id">ChinaXiv:${escapeHtml(hit.id || '')}</span>
            ${subjectTags ? `<span class="paper-subjects">${subjectTags}</span>` : ''}
            <div class="paper-links">
              <a href="/items/${hit.id}/" class="btn-sm">Abstract</a>
            </div>
          </div>
          <p class="paper-abstract">${highlightTerms((hit.abstract || '').slice(0, 300), currentQuery)}${(hit.abstract || '').length > 300 ? '…' : ''}</p>
        </article>`;
      }).join('');
    }
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
    if (!miniSearch) { results.innerHTML = '<div class="res search-loading"><div>Loading search index...</div></div>'; return; }
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

  if (categoryFilter) categoryFilter.addEventListener('change', applyFiltersAndRender);
  if (dateFilter) dateFilter.addEventListener('change', applyFiltersAndRender);
  if (sortOrder) sortOrder.addEventListener('change', () => { userChangedSort = true; applyFiltersAndRender(); });
  if (figuresFilter) figuresFilter.addEventListener('change', applyFiltersAndRender);
  if (searchBtn) searchBtn.addEventListener('click', () => performSearch(input.value));

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
