(() => {
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
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
      // If there's a URL query, perform search immediately
      if (urlQuery) {
        performSearch(urlQuery);
      }
    })
    .catch(() => {
      // Fallback to uncompressed
      fetch('search-index.json').then(r => r.json()).then(data => {
        index = data;
        if (urlQuery) {
          performSearch(urlQuery);
        }
      }).catch(() => {});
    });
  
  function performSearch(query) {
    const q = query.trim().toLowerCase();
    if (!q) { results.innerHTML = ''; return; }
    
    const out = [];
    for (const it of index) {
      const hay = [it.title, it.authors, it.abstract, it.subjects].join(' ').toLowerCase();
      if (hay.includes(q)) out.push(it);
      if (out.length >= 50) break;
    }
    
    if (out.length === 0) {
      results.innerHTML = '<div class="res"><div>No papers found matching your search.</div></div>';
    } else {
      results.innerHTML = out.map(it => `
        <div class="res">
          <div><a href="/items/${it.id}/"><strong>${escapeHtml(it.title || '')}</strong></a></div>
          <div class="meta">${it.date || ''} — ${escapeHtml(it.authors || '')}</div>
          <div>${escapeHtml((it.abstract || '').slice(0, 280))}…</div>
        </div>
      `).join('');
    }
  }
  
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim().toLowerCase();
    if (!q) { results.innerHTML = ''; return; }
    timer = setTimeout(() => {
      performSearch(q);
    }, 120);
  });

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
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

function showCopyFeedback() {
  // Create a temporary feedback element
  const feedback = document.createElement('div');
  feedback.textContent = 'Copied!';
  feedback.style.cssText = `
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: var(--arxiv-blue);
    color: white;
    padding: 12px 24px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    z-index: 1000;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    animation: fadeInOut 2s ease-in-out;
  `;
  
  // Add CSS animation
  const style = document.createElement('style');
  style.textContent = `
    @keyframes fadeInOut {
      0% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); }
      20% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
      80% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
      100% { opacity: 0; transform: translate(-50%, -50%) scale(0.8); }
    }
  `;
  document.head.appendChild(style);
  
  document.body.appendChild(feedback);
  
  // Remove feedback after animation
  setTimeout(() => {
    if (feedback.parentNode) {
      feedback.parentNode.removeChild(feedback);
    }
    if (style.parentNode) {
      style.parentNode.removeChild(style);
    }
  }, 2000);
}
