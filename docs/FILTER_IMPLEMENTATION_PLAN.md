# Client-Side Search & Filter Implementation Plan

**Status:** ✅ Ready for Implementation (Codex-hardened)
**Created:** 2025-12-09
**Version:** 2.0

## Quick Links

- **Full Plan:** `/Users/alexanderhuth/.claude/plans/jiggly-moseying-waterfall.md`
- **Related:** `TODO.md` (Future server-side migration)
- **Tests:** `tests/test_filter_system.py` (to be created)

## Executive Summary

This document outlines the plan to fix the broken filtering system by implementing a clean, maintainable client-side search architecture using MiniSearch.

**Approach:** Client-side (sufficient for 870 papers, scalable to 5K+)
**Timeline:** 1-2 days implementation + testing
**Migration Trigger:** >5,000 papers (move to server-side)

### Codex Review Incorporated (v2.0)

**Critical fixes added:**
- ✅ XSS prevention (DOM API instead of innerHTML)
- ✅ Date normalization with validation
- ✅ State/URL loop prevention (skipPushState flag)
- ✅ MiniSearch ranking preservation
- ✅ Category/subject normalization
- ✅ Accessibility improvements (ARIA, live regions)
- ✅ Test strategy updated (Playwright + Jest)

## Key Design Decisions

1. **Single Source of Truth:** Central `filterState` object
2. **Client-Side Search:** MiniSearch library (already loaded)
3. **URL State:** All filters reflected in URL for shareability
4. **Incremental Implementation:** 7 phases, test after each
5. **Clean Start:** New branch from `main`, discard broken patches

## Implementation Phases

1. **Phase 1:** Clean up & foundation
2. **Phase 2:** Category tabs
3. **Phase 3:** Search box
4. **Phase 4:** Advanced modal
5. **Phase 5:** Core filter function
6. **Phase 6:** URL state management
7. **Phase 7:** Testing

## Out of Scope (Future)

- Field-specific search (title only, author only)
- Figures-only filter
- Filter indicator pills
- Subcategory drilling
- Sort options
- Analytics tracking
- Server-side migration (when >5K papers)

## File Modifications

**Primary:**
- `assets/site.js` - Complete rewrite (~500 lines)

**Secondary:**
- `src/templates/index.html` - Verify IDs
- `tests/test_filter_system.py` - New tests
- `TODO.md` - Move out-of-scope items

## Review Process

1. ✅ Plan completed
2. ⏳ Gemini review (architecture, edge cases)
3. ⏳ Codex review (JavaScript patterns, security)
4. ⏳ Address feedback
5. ⏳ Begin implementation

---

**For full details, see:** `/Users/alexanderhuth/.claude/plans/jiggly-moseying-waterfall.md`
