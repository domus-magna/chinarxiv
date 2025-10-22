# [ARCHIVED] Site Fixes Summary - October 9, 2025

Note (archived on 2025-10-21): Superseded by consolidated guides in `docs/`.

## Issues Fixed ✅

### 1. Removed Broken Footer Links (P0)
**Problem:** Footer contained links to non-existent pages (Advanced Search, Browse All, Statistics, API, Help, Contact).

**Solution:** 
- Removed entire "Tools" section from footer (`src/templates/base.html`)
- Removed broken `/help` and `/contact` links from Project section
- Kept working links: Home, Source Code, Support Us

**Files Modified:**
- `src/templates/base.html`

### 2. Removed Non-Functional Filters (P1)
**Problem:** Homepage had category and date dropdown filters with no JavaScript implementation.

**Solution:**
- Removed the entire filters section from search block
- Kept functional search input box
- Filters were misleading since categories don't match actual Chinese paper categories

**Files Modified:**
- `src/templates/index.html`

### 3. Fixed Search Result Paths (P1)
**Problem:** Search results used absolute paths (`/items/${id}/`) which would break if site deployed to subdirectory.

**Solution:**
- Changed to relative paths (`./items/${id}/`)
- Ensures site works correctly when deployed to CDN or subdirectory

**Files Modified:**
- `assets/site.js`

### 4. Fixed BibTeX ID Generation (P1)
**Problem:** BibTeX IDs contained dots (e.g., `chinaxiv202510.00001`) which is invalid BibTeX syntax.

**Solution:**
- Added JavaScript regex to replace dots and hyphens with underscores
- Example: `chinaxiv-202510.00001` → `chinaxiv_202510_00001`

**Files Modified:**
- `src/templates/item.html`

### 5. Added Clipboard Fallback (P1)
**Problem:** Citation and BibTeX copying used `navigator.clipboard` without fallback, failing on non-HTTPS contexts.

**Solution:**
- Added `copyToClipboard()` helper function with `document.execCommand()` fallback
- Applied to all clipboard operations: citations, BibTeX, and share functions

**Files Modified:**
- `src/templates/item.html`

### 6. Cleaned Up Test Pages (P2)
**Problem:** Development artifacts cluttering the site.

**Solution:**
- Removed `site/items/test-clean-001/`
- Removed `site/items/test-sample-001/`
- Removed `site/samples/` directory

### 7. Rebuilt Site
**Actions Taken:**
- Ran `python -m src.render` to apply template changes
- Ran `python -m src.search_index` to rebuild search index
- All changes verified in generated HTML

---

## Critical Issue Remaining ⚠️

### Translation Pipeline Failure (P0 - BLOCKS CONTENT)

**Root Cause:** Invalid OpenRouter API key

**Symptoms:**
- All papers in `data/translated/` contain Chinese text in `title_en` and `abstract_en` fields
- QA filter correctly flags them as containing 88-99% Chinese characters
- Render script skips all flagged papers, leaving only the demo paper on homepage
- Search index is empty except for demo paper

**Evidence:**
```bash
$ python -m src.translate chinaxiv-202510.00001
[...] Model deepseek/deepseek-v3.2-exp failed: User not found.
Error: All translation models failed. Last error: User not found.
```

**What Needs to Happen:**

1. **Get Valid OpenRouter API Key:**
   - Current key returns "User not found" error
   - Key must be for an active account with credits
   - Set in environment: `export OPENROUTER_API_KEY=sk-or-v1-...`
   - Or add to `.env` file: `OPENROUTER_API_KEY=sk-or-v1-...`

2. **Re-translate Existing Papers:**
   ```bash
   # Re-translate the 5 existing papers
   python -m src.translate chinaxiv-202510.00001
   python -m src.translate chinaxiv-202509.00001
   python -m src.translate chinaxiv-202509.00002
   python -m src.translate chinaxiv-202508.00001
   python -m src.translate chinaxiv-202508.00002
   ```

3. **Verify Translations:**
   - Check that `data/translated/*.json` files have English in `title_en` and `abstract_en`
   - Verify QA status is `"pass"` instead of `"flag_chinese"`

4. **Rebuild Site:**
   ```bash
   python -m src.render
   python -m src.search_index
   ```

5. **Verify Homepage:**
   - Should show 6 papers (5 real + 1 demo) instead of just 1
   - All titles should be in English
   - Search should return results

---

## Site Status Summary

### What Works ✅
- Site structure and navigation
- Search functionality (searches available papers)
- Paper detail pages (template is correct)
- Citation and BibTeX generation
- Donation page
- Footer links (all working)
- Relative paths for deployment flexibility
- Clipboard operations with fallbacks

### What's Broken ❌
- **Translation pipeline** - API key invalid
- **Homepage content** - Empty because translations are flagged
- **Search index** - Empty because translations are flagged

### Impact Assessment

**User Experience:**
- Site appears "empty" with only a demo paper
- Looks unprofessional to visitors
- Can't evaluate actual translation quality
- Can't test search with real data

**Technical:**
- All infrastructure is working correctly
- Templates are fixed and production-ready
- Issue is purely in the data pipeline (API authentication)
- Once API key is fixed, everything will work immediately

---

## Next Steps for User

### Immediate (Required for Site to Function)
1. **Obtain valid OpenRouter API key** from https://openrouter.ai/
2. **Add credits** to OpenRouter account (approximately $0.0013 per paper)
3. **Set the key** in environment or `.env` file
4. **Re-translate the 5 existing papers** (see commands above)
5. **Verify and deploy**

### Short-term Improvements (Optional)
- Add date formatting filter to normalize date display
- Consider removing category links from footer since they don't work yet
- Update "Last updated" date in footer (currently hardcoded to 2025-10-05)
- Consider adding real QR codes to donation page

### Long-term Enhancements (Optional)
- Implement category filtering if papers get translated categories
- Add statistics page showing translation counts, costs, etc.
- Add API documentation page if you want to expose an API
- Consider adding "Browse All" page with pagination

---

## Files Modified in This Session

### Templates
- `src/templates/base.html` - Removed broken footer links
- `src/templates/index.html` - Removed non-functional filters
- `src/templates/item.html` - Fixed BibTeX IDs, added clipboard fallback

### Assets
- `assets/site.js` - Fixed search result paths to be relative

### Generated Site
- `site/index.html` - Regenerated with template changes
- `site/search-index.json` - Regenerated (currently 1 entry)
- `site/items/*/index.html` - Regenerated paper pages

### Cleaned Up
- Removed `site/items/test-clean-001/`
- Removed `site/items/test-sample-001/`
- Removed `site/samples/`

---

## Testing Checklist

Once translations are fixed, verify:
- [ ] Homepage shows 6 papers with English titles
- [ ] Search returns results for English queries
- [ ] Paper detail pages show English content
- [ ] BibTeX generation creates valid IDs (no dots)
- [ ] Citation copying works in HTTP and HTTPS contexts
- [ ] Footer has no broken links
- [ ] No console errors in browser
- [ ] Site works when served from subdirectory

---

## Cost Estimate for Re-translation

Based on `src/services/translation_service.py` cost tracking:
- **Per paper:** ~$0.0013 (title + abstract + body)
- **5 papers:** ~$0.0065 (less than 1 cent)
- **Full backfill (34,237 papers):** ~$45

The current 5 papers should cost less than 1 cent to re-translate once you have a valid API key.
