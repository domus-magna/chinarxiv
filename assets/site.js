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
  const categoryFilter = document.getElementById('category-filter');
  const dateFilter = document.getElementById('date-filter');
  if (!input || !results) return;

  let miniSearch = null;
  let lastSearchResults = [];
  let currentQuery = '';

  // Category keywords for filtering
  const categoryMap = {
    cs: ['computer science', 'software', 'programming', 'algorithm', 'artificial intelligence', 'machine learning'],
    math: ['mathematics', 'mathematical', 'algebra', 'geometry', 'calculus', 'statistics'],
    physics: ['physics', 'quantum', 'mechanics', 'relativity', 'particle'],
    biology: ['biology', 'biological', 'genetics', 'cell', 'organism'],
    chemistry: ['chemistry', 'chemical', 'organic', 'inorganic']
  };

  // Date filter: days ago lookup
  const dateDays = { today: 0, week: 7, month: 30, year: 365 };

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
    miniSearch = new MiniSearch({
      fields: ['title', 'authors', 'abstract', 'subjects'],
      storeFields: ['id', 'title', 'authors', 'abstract', 'subjects', 'date'],
      searchOptions: { boost: { title: 3, authors: 2, subjects: 1.5, abstract: 1 }, fuzzy: 0.2, prefix: true }
    });
    miniSearch.addAll(docs);
  }

  // Load index (try compressed first)
  fetch('search-index.json.gz')
    .then(r => r.ok ? r.arrayBuffer().then(buf => JSON.parse(pako.inflate(new Uint8Array(buf), { to: 'string' }))) : fetch('search-index.json').then(r => r.json()))
    .then(data => { initMiniSearch(data); if (urlQuery) performSearch(urlQuery); })
    .catch(() => fetch('search-index.json').then(r => r.json()).then(data => { initMiniSearch(data); if (urlQuery) performSearch(urlQuery); })
      .catch(() => { results.innerHTML = '<div class="res"><div>Failed to load search index.</div></div>'; }));

  // Apply filters and render
  function applyFiltersAndRender() {
    const cat = categoryFilter?.value || '';
    const dateRange = dateFilter?.value || '';

    const filtered = lastSearchResults.filter(hit => {
      // Category filter
      if (cat && !(categoryMap[cat] || []).some(kw => (hit.subjects || '').toLowerCase().includes(kw))) return false;
      // Date filter
      if (dateRange && dateDays[dateRange] !== undefined) {
        const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - dateDays[dateRange]);
        if (new Date(hit.date) < cutoff) return false;
      }
      return true;
    });

    renderResults(filtered, cat || dateRange);
  }

  // Highlight search terms
  function highlightTerms(text, query) {
    if (!query || !text) return escapeHtml(text || '');
    const escaped = escapeHtml(text);
    const terms = query.toLowerCase().split(/\s+/).filter(t => t.length > 1);
    if (!terms.length) return escaped;
    const pattern = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    return escaped.replace(new RegExp(`(${pattern})`, 'gi'), '<mark>$1</mark>');
  }

  // Render results
  function renderResults(hits, hasFilters) {
    if (!hits.length) {
      const msg = hasFilters ? 'No papers match with selected filters. Try adjusting them.' : 'No papers found. Try different keywords.';
      results.innerHTML = `<div class="res"><div>${msg}</div></div>`;
    } else {
      results.innerHTML = hits.map(hit => `
        <div class="res">
          <div><a href="/items/${hit.id}/"><strong>${highlightTerms(hit.title || '', currentQuery)}</strong></a></div>
          <div class="meta">${hit.date || ''} — ${escapeHtml(hit.authors || '')}</div>
          <div>${highlightTerms((hit.abstract || '').slice(0, 280), currentQuery)}…</div>
        </div>`).join('');
    }
  }

  function performSearch(query) {
    currentQuery = query.trim();
    if (!currentQuery) { results.innerHTML = ''; lastSearchResults = []; return; }
    if (!miniSearch) { results.innerHTML = '<div class="res search-loading"><div>Loading search index...</div></div>'; return; }
    lastSearchResults = miniSearch.search(currentQuery, { limit: 100 });
    applyFiltersAndRender();
  }

  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    if (!input.value.trim()) { results.innerHTML = ''; return; }
    timer = setTimeout(() => performSearch(input.value), 120);
  });

  if (categoryFilter) categoryFilter.addEventListener('change', applyFiltersAndRender);
  if (dateFilter) dateFilter.addEventListener('change', applyFiltersAndRender);

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
    background: var(--arxiv-blue, #2563eb);
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
