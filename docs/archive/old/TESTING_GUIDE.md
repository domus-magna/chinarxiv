# [ARCHIVED] Testing Guide - Site Fixes

Note (archived on 2025-10-21): Testing instructions are consolidated in `docs/CLAUDE.md` and `docs/DEVELOPMENT.md`.

## Quick Test (No API Key Needed)

Test all the non-translation fixes immediately:

```bash
# Start local server
cd /Users/alexanderhuth/chinaxiv-english
python3 -m http.server -d site 8001 &

# Open in browser
open http://localhost:8001
```

### What to Verify

1. **Footer Links** - Should only see:
   - Browse section (Recent Papers, Computer Science, etc.)
   - About section
   - Project section (Source Code, Support Us)
   - ❌ No "Tools" section
   - ❌ No "Help" or "Contact" links

2. **Homepage**
   - Search box present and functional
   - ❌ No category/date filter dropdowns
   - Only demo paper visible (until translations fixed)

3. **Search Functionality**
   - Type "demo" in search box
   - Should show demo paper result
   - Click result - should go to paper page (not 404)

4. **Paper Detail Page**
   - Visit http://localhost:8001/items/demo-0001/
   - Click "Copy Citation" - should work
   - Click "Export BibTeX" - should copy valid BibTeX (check for underscores instead of dots)
   - Check browser console - no errors

## Full Test (With Valid API Key)

Once you have a valid OpenRouter API key:

### 1. Set Up API Key

```bash
cd /Users/alexanderhuth/chinaxiv-english
source .venv/bin/activate

# Option A: Export in shell
export OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE

# Option B: Add to .env file
echo "OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE" >> .env
```

### 2. Verify API Key

```bash
# Check if key is accessible
python3 -c "import os; print('Key set!' if os.getenv('OPENROUTER_API_KEY') else 'Key NOT set')"

# Test with env diagnostic tool
python -m src.tools.env_diagnose --validate
```

### 3. Re-translate Papers

```bash
# Translate one paper first to test
python -m src.translate chinaxiv-202510.00001

# If successful, translate the rest
python -m src.translate chinaxiv-202509.00001
python -m src.translate chinaxiv-202509.00002
python -m src.translate chinaxiv-202508.00001
python -m src.translate chinaxiv-202508.00002
```

**Expected Output:**
```
[...] Attempting translation with model: deepseek/deepseek-v3.2-exp
[...] Translation saved to: data/translated/chinaxiv-202510.00001.json
```

**Success Indicators:**
- No "User not found" error
- File saved to `data/translated/`
- Check file: `title_en` and `abstract_en` should be in English

### 4. Verify Translation Quality

```bash
# Quick check of one translation
cat data/translated/chinaxiv-202510.00001.json | python3 -m json.tool | grep -A2 '"title_en"'
```

Should show English title, not Chinese characters.

### 5. Rebuild Site

```bash
# Regenerate site HTML
python -m src.render

# Regenerate search index
python -m src.search_index
```

**Expected Output:**
```
[...] Rendered site with 6 items → site/
[...] Wrote search index with 6 entries → site/search-index.json
```

If you still see "Skipping flagged translation", the translation didn't work properly.

### 6. Test Full Site

```bash
# Restart server if running
pkill -f "http.server.*8001"
python3 -m http.server -d site 8001 &

# Open in browser
open http://localhost:8001
```

### What to Verify

1. **Homepage**
   - Shows 6 papers (5 real + 1 demo)
   - All titles are in English
   - Authors and subjects visible
   - Abstracts in English

2. **Search**
   - Search for keywords from paper titles
   - Should return relevant results
   - Click results - navigate to correct papers

3. **Paper Detail Pages**
   - Visit multiple paper pages
   - Titles, abstracts, and full text in English
   - Copy citation - should work
   - Export BibTeX - valid format with underscores
   - No Chinese characters visible (unless in math notation)

4. **Browser Console**
   - Open DevTools (F12)
   - Check Console tab - should be no errors
   - Check Network tab - all resources loading

## Troubleshooting

### Problem: "User not found" error

**Solution:** API key is invalid
- Verify key at https://openrouter.ai/keys
- Check account has credits
- Make sure key starts with `sk-or-v1-`

### Problem: Translations still in Chinese

**Possible Causes:**
1. API call succeeded but returned Chinese (rare)
2. Wrong field being populated
3. Model not translating properly

**Debug:**
```bash
# Check raw translation output
cat data/translated/chinaxiv-202510.00001.json | python3 -m json.tool | head -20

# Look for _qa_status field
grep "_qa_status" data/translated/chinaxiv-202510.00001.json
```

Should show `"_qa_status": "pass"`, not `"flag_chinese"`.

### Problem: Homepage still empty

**Solution:** Translations are still flagged
- Delete old translations: `rm data/translated/chinaxiv-*.json`
- Re-translate with valid API key
- Rebuild site

### Problem: Search not working

**Possible Causes:**
1. Search index not rebuilt
2. JavaScript error

**Debug:**
```bash
# Check search index
cat site/search-index.json | python3 -m json.tool | head -20

# Should show array of papers with English titles
```

### Problem: BibTeX still has dots

**Solution:** Browser cache
- Hard refresh: Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac)
- Or clear browser cache
- Re-build site: `python -m src.render`

## Expected Costs

If using OpenRouter API with DeepSeek V3.2:
- **5 papers:** ~$0.01 (1 cent)
- **100 papers:** ~$0.13
- **1000 papers:** ~$1.30

Very affordable for testing!

## Next Steps

After successful testing:
1. Commit changes to git
2. Push to GitHub
3. CI/CD will deploy to Cloudflare Pages
4. Verify production site

## Need Help?

If you encounter issues:
1. Check `SITE_FIXES_SUMMARY.md` for details
2. Review logs for error messages
3. Test API key with `env_diagnose --validate`
4. Check QA status in translated JSON files
