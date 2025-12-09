# ChinaRxiv English - Feature Roadmap

## In Progress

- [x] Category filter tabs - COMPLETED

## Planned Features

### Advanced Filters Modal
**Status**: UI complete, JavaScript needed
**Files**: `src/templates/index.html:113-233`, `site/assets/site.js`

**Requirements**:
- [ ] Wire up modal open/close handlers
- [ ] Field-specific search (title/author/abstract only)
- [ ] Date range filters (7d/30d/90d/1y)
- [ ] Category accordion (hierarchical tree with expand/collapse)
- [ ] Figures-only filter
- [ ] Sync modal state with current filters
- [ ] Filter indicator pills below search bar
- [ ] URL state: `?category=X&date=Y&figures=true`

**Implementation Notes**:
- Modal HTML/CSS exists, zero JavaScript wired
- Should integrate with existing `applyFiltersAndRender()` in `site/assets/site.js`
- Paper count format: "Showing X papers" (consistent with tabs)
- See inline comments in `site/assets/site.js:372-385` for integration approach

### Filter Indicator Pills
**Status**: HTML exists, needs JavaScript
**Files**: `src/templates/index.html:45-48`

**Requirements**:
- [ ] Show active filters as dismissible pills
- [ ] Click X to remove individual filter
- [ ] "Clear all" button when multiple filters active
- [ ] Pills update in real-time as filters change

**Implementation Notes**:
- Container exists: `<div class="filter-indicators" id="filterIndicators">`
- Should display pills for: active category, date range, figures-only
- Example: `[AI & Computer Science ×] [Last 30 days ×] [With figures ×] [Clear all]`

### Subcategory Drilling
**Status**: Planned
**Description**: Click child subjects in modal accordion to filter by specific subcategories

**Requirements**:
- [ ] Make child subjects clickable in category accordion
- [ ] Filter by specific subcategory (e.g., "Nuclear Physics" vs "Physics")
- [ ] Breadcrumb showing filter hierarchy
- [ ] URL state support: `?category=physics&subcategory=nuclear_physics`

**Implementation Notes**:
- Category taxonomy supports hierarchical structure (`src/category_taxonomy.json`)
- Child subjects already displayed in modal accordion (when implemented)
- May require extending `categorySubjects` mapping to support subcategory IDs

### Analytics Tracking
**Status**: Future consideration
**Description**: Track popular categories and search terms for content strategy

**Requirements**:
- [ ] Track category tab clicks
- [ ] Track search queries
- [ ] Track figure translation requests (already implemented via KV)
- [ ] Dashboard showing popular topics

**Implementation Notes**:
- Could use Cloudflare Analytics or custom KV-based tracking
- Privacy-preserving (no PII)
- Helps prioritize translation efforts

## Completed Features

- [x] Server-side category taxonomy (ai_computing, physics, psychology)
- [x] Hierarchical category structure with counts
- [x] Search index with MiniSearch
- [x] Basic paper rendering and display
- [x] Category filter tabs with URL state (Dec 2025)
- [x] Dynamic paper count updates (Dec 2025)
- [x] Browser back/forward support for category filters (Dec 2025)

## Technical Debt

- [ ] Performance testing with 5K+ papers (current: ~500)
- [ ] Add caching for category-to-subjects mapping
- [ ] Consider lazy loading for very large datasets
- [ ] Add Playwright test coverage for category filtering
- [ ] Consider memoizing filter results for faster back/forward navigation

## Search Architecture Migration (When Dataset > 5,000 Papers)

### Current State (Client-Side)
- **Technology**: MiniSearch library for in-memory full-text search
- **Index file**: `search-index.json.gz` (~5 KB at 8 papers, will be ~2.3 MB at 500 papers, ~23 MB at 40,000 papers)
- **Performance**: Works well for <5K papers but will degrade beyond that
- **Browser memory**: ~1 MB now, projected ~80 MB at 40K papers
- **Current stats**: 8 papers (dev), ~500 papers (prod)

### Trigger for Migration
- **Hard limit**: When compressed index exceeds 10 MB (~15,000 papers)
- **Soft limit**: When search latency exceeds 500ms (~10,000 papers)
- **Memory limit**: Mobile devices struggle at ~50 MB index size

### Server-Side Architecture Plan

#### Phase 1: API Foundation
**File**: Create `workers/search-api/src/index.ts`

Implement Cloudflare Workers search endpoint:
```
GET /api/search?q=<query>&category=<cat>&page=<n>&limit=<l>
```

**Technology choices**:
- **Option A**: Cloudflare D1 (SQL) with FTS5 full-text search
  - ✅ Mature FTS capabilities
  - ✅ Supports complex queries (AND/OR/NOT, phrase search)
  - ✅ Built-in relevance ranking
  - ❌ SQL overhead for simple queries

- **Option B**: Cloudflare KV with custom indexing
  - ✅ Lightning-fast key lookup
  - ✅ Good for faceted search (category, date filters)
  - ❌ Limited full-text capabilities
  - ❌ Need custom relevance scoring

- **Recommendation**: D1 with FTS5 for full-text, KV for metadata/filters

#### Phase 2: Pagination
**Files**: `assets/site.js`, `src/templates/index.html`

Add pagination UI:
- Replace infinite scroll with page numbers (1, 2, 3...10)
- Show "Showing 1-50 of 3,542 papers"
- Implement URL-based pagination: `?page=2&q=neural&category=ai_computing`

#### Phase 3: Progressive Enhancement
Keep client-side search as fallback:
- If API fails → fallback to client-side MiniSearch
- Cache recent searches in localStorage
- Prefetch next page in background

#### Phase 4: Analytics
Track search patterns:
- Most searched terms
- Zero-result queries (improve indexing)
- Category filter usage
- Page depth (how far users scroll)

### Migration Checklist
- [ ] Create D1 database schema
- [ ] Implement search API in Workers
- [ ] Build search index population script
- [ ] Add pagination UI
- [ ] Update search.js to use API
- [ ] Add fallback to client-side
- [ ] Performance testing (compare before/after)
- [ ] Deploy behind feature flag
- [ ] Gradual rollout (10% → 50% → 100%)
- [ ] Remove client-side search code

### Estimated Timeline
- API development: 2-3 days
- Frontend integration: 1-2 days
- Testing & optimization: 1-2 days
- **Total**: 1 week

### Cost Analysis
- **D1 pricing**: $5/month for 25 GB storage (sufficient for 100K papers)
- **Workers requests**: First 10M free, then $0.50 per million
- **Expected cost at 40K papers**: ~$5-10/month

### Code Migration Touchpoints
See inline comments in `assets/site.js` at these locations:
- Line 76-89: MiniSearch initialization (scalability note)
- Line 135-146: `applyFiltersAndRender()` function (filtering note)
- Line 292-301: `performSearch()` function (search API migration path)
- Line 422-431: Advanced modal filters (modal-to-API integration)

Also see scalability warning in `src/search_index.py` docstring.
