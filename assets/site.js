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
  const searchBtn = document.querySelector('.search-btn');
  if (!input || !results) return;
  let index = [];

  // Check for URL search parameter
  const urlParams = new URLSearchParams(window.location.search);
  const urlQuery = urlParams.get('q');
  if (urlQuery && input) {
    input.value = urlQuery;
  }

  // Try compressed index first, fallback to uncompressed
  fetch('search-index.json.gz')
    .then(r => {
      if (r.ok) {
        return r.arrayBuffer().then(buf => {
          const decompressed = pako.inflate(new Uint8Array(buf), { to: 'string' });
          return JSON.parse(decompressed);
        });
      } else {
        return fetch('search-index.json').then(r => r.json());
      }
    })
    .then(data => {
      index = data;
      // Show all papers on load, or perform URL query search
      performSearch(urlQuery || '');
    })
    .catch(() => {
      // Fallback to uncompressed
      fetch('search-index.json').then(r => r.json()).then(data => {
        index = data;
        performSearch(urlQuery || '');
      }).catch(() => {});
    });

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
  }

  function formatDate(isoDate) {
    if (!isoDate) return '';
    try {
      const date = new Date(isoDate);
      return date.toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric'
      });
    } catch { return isoDate; }
  }

  function categorizeSubject(subject) {
    const s = subject.toLowerCase();
    if (s.includes('artificial intelligence') || s.includes('machine learning') ||
        s.includes('deep learning') || s.includes('neural') || s.includes('nlp') ||
        s.includes('computer vision') || s.includes('ai ') || s.includes(' ai')) return 'ai';
    if (s.includes('computer science') || s.includes('software') ||
        s.includes('algorithm') || s.includes('information science')) return 'cs';
    if (s.includes('psychology') || s.includes('cognitive')) return 'psychology';
    if (s.includes('engineering') || s.includes('technical')) return 'engineering';
    return '';
  }

  function matchesCategory(subjects, category) {
    if (!category) return true; // "All Categories"
    const s = (subjects || '').toLowerCase();
    switch (category) {
      case 'physics': return s.includes('physics') || s.includes('nuclear') || s.includes('optics');
      case 'engineering': return s.includes('engineering') || s.includes('geology') || s.includes('technical');
      case 'psychology': return s.includes('psychology') || s.includes('cognitive');
      case 'cs': return s.includes('computer') || s.includes('information') || s.includes('software') || s.includes('algorithm');
      case 'astronomy': return s.includes('astronomy');
      default: return true;
    }
  }

  function renderResult(it) {
    const tags = (it.subjects || '').split(',')
      .map(s => s.trim())
      .filter(s => s)
      .map(s => {
        const cat = categorizeSubject(s);
        const escaped = escapeHtml(s).replace(/'/g, "\\'");
        return `<span class="subject-tag" ${cat ? `data-category="${cat}"` : ''} onclick="searchSubject('${escaped}')">${escapeHtml(s)}</span>`;
      }).join('');

    return `
      <div class="res">
        <div class="res-title"><a href="/items/${it.id}/">${escapeHtml(it.title || '')}</a></div>
        <div class="res-meta">${formatDate(it.date)} — ${escapeHtml(it.authors || '')}</div>
        <div class="res-abstract">${escapeHtml((it.abstract || '').slice(0, 280))}…</div>
        ${tags ? `<div class="res-tags">${tags}</div>` : ''}
      </div>
    `;
  }

  function performSearch(query) {
    const q = (query || '').trim().toLowerCase();
    const category = categoryFilter ? categoryFilter.value : '';

    const out = [];
    for (const it of index) {
      // Check category filter first
      if (!matchesCategory(it.subjects, category)) continue;

      // If query provided, check text match
      if (q) {
        const hay = [it.title, it.authors, it.abstract, it.subjects].join(' ').toLowerCase();
        if (!hay.includes(q)) continue;
      }

      out.push(it);
      if (out.length >= 50) break;
    }

    if (out.length === 0) {
      results.innerHTML = '<div class="search-results-count">No papers found matching your search.</div>';
    } else {
      results.innerHTML = `<div class="search-results-count">Found ${out.length} paper${out.length > 1 ? 's' : ''}</div>` +
        out.map(renderResult).join('');
    }
  }

  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      performSearch(input.value);
    }, 120);
  });

  // Category filter triggers immediate search
  if (categoryFilter) {
    categoryFilter.addEventListener('change', () => {
      performSearch(input.value);
    });
  }

  // Search button triggers search
  if (searchBtn) {
    searchBtn.addEventListener('click', () => {
      performSearch(input.value);
    });
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

// Copy to clipboard functionality for donation page
function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    // Use modern clipboard API
    navigator.clipboard.writeText(text).then(() => {
      showCopyFeedback();
    }).catch(() => {
      fallbackCopyToClipboard(text);
    });
  } else {
    // Fallback for older browsers
    fallbackCopyToClipboard(text);
  }
}

function fallbackCopyToClipboard(text) {
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
    showCopyFeedback();
  } catch (err) {
    console.error('Failed to copy text: ', err);
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
