# ChinaRxiv English - Feature Roadmap

## In Progress

- [x] Category filter tabs - COMPLETED

## Planned Features

### Advanced Filters Modal
**Status**: Server‑side modal working; enhancements optional
**Files**: `src/templates/index.html`, `app/routes.py`, `app/database.py`

**Requirements**:
- [ ] Field-specific search (title/author/abstract only)
- [ ] Date quick‑ranges (7d/30d/90d/1y) as helper buttons
- [ ] Optional subcategory drilldown breadcrumbs
- [ ] Preserve URL state (already supported for category/date/figures/subjects)

**Implementation Notes**:
- Modal open/close, accordion expand/collapse, and server‑side filtering are implemented.
- Remaining items are UX polish, not blockers.

### Filter Indicator Pills
**Status**: Completed (server‑rendered)
**Files**: `src/templates/index.html`

**Requirements**:
- [x] Show active filters as pills with remove links
- [x] Clear‑all resets to `/`

**Implementation Notes**:
- Implemented in Jinja with simple query‑param preservation.

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

## Cloudflare Workers Migration to Railway

**Status**: Planned (Phase 2)
**Context**: Cloudflare Pages static site generation has been removed. Workers remain for backward compatibility.

### Workers to Migrate

| Worker | Current Location | Target | Notes |
|--------|-----------------|--------|-------|
| `backfill-api` | `workers/backfill-api/` | Flask route `/api/backfill` | Triggers backfill workflows |
| `backfill-orchestrator` | `workers/backfill-orchestrator/` | Flask route or cron job | Coordinates backfill |
| `report-api` | `workers/report-api/` | Flask route `/api/reports` | Pipeline reports |
| `redirect` | `workers/redirect/` | Cloudflare redirect rules | Simple domain redirects |

### Pages Functions to Migrate

| Function | Current Location | Target | Notes |
|----------|-----------------|--------|-------|
| `request-figure-translation` | `functions/api/` | Flask route `/api/request-figure` | Uses KV for storage |
| `request-text-translation` | `functions/api/` | Flask route `/api/request-text` | Uses KV for storage |

### Storage Migration

- [ ] Replace Cloudflare KV with PostgreSQL tables for figure/text requests
- [ ] Create `translation_requests` table:
  ```sql
  CREATE TABLE translation_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id TEXT NOT NULL,
    request_type TEXT CHECK (request_type IN ('figure', 'text')),
    ip_hash TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
  );
  CREATE INDEX idx_requests_paper_id ON translation_requests(paper_id);
  CREATE INDEX idx_requests_created ON translation_requests(created_at);
  ```
- [ ] Update aggregate scripts to query PostgreSQL

### Migration Steps

1. [ ] Create Flask routes for each worker endpoint
2. [ ] Add PostgreSQL table for translation requests
3. [ ] Update frontend to use Railway API endpoints
4. [ ] Test all endpoints on Railway staging
5. [ ] Update DNS/routing to point to Railway
6. [ ] Deprecate Cloudflare Workers (keep redirect rules)

## Technical Debt

- [ ] Performance testing with 5K+ papers (current: ~500)
- [ ] Add caching for category-to-subjects mapping
- [ ] Consider lazy loading for very large datasets
- [ ] Add Playwright test coverage for category filtering
- [ ] Consider memoizing filter results for faster back/forward navigation
