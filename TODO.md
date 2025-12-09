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
