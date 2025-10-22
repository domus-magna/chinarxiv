# [ARCHIVED] Batch Translation System Guide

Note (archived on 2025-10-21): Batch translation is documented in `docs/WORKFLOWS.md` and `docs/DEPLOYMENT.md`. This guide is kept for historical context.

Complete guide for running fully online batch translation with integrated QA filtering.

## Overview

The batch translation system processes papers in configurable batches via GitHub Actions, with automatic QA filtering to ensure translation quality. All processing happens online - no local dependencies required.

### Key Features

- ✅ **Fully online**: Runs entirely on GitHub Actions (domus-magna org with larger runners)
- ✅ **Scalable**: Process 33k+ papers in manageable batches
- ✅ **QA integrated**: Automatic filtering prevents bad translations from reaching the site
- ✅ **Resumable**: Can restart from checkpoint if workflows fail
- ✅ **Cost-effective**: Free GitHub Actions minutes for public repos
- ✅ **Transparent**: All progress tracked in Git, visible in workflow logs

## Architecture

```
┌─────────────────────────────────────────────────┐
│ 1. Initialize Queue (One-time)                 │
│    scripts/init_cloud_queue.py                  │
│    → Creates data/cloud_jobs.json               │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│ 2. Batch Translation Worker                     │
│    .github/workflows/translate_batch.yml        │
│    • Claims batch from queue                    │
│    • Translates papers (80 workers)             │
│    • Runs QA filter on each                     │
│    • Saves: passed → data/translated/           │
│            flagged → data/flagged/              │
│    • Commits progress to queue                  │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│ 3. Orchestrator (Optional)                      │
│    .github/workflows/translate_orchestrator.yml │
│    • Runs multiple batches sequentially         │
│    • Monitors queue progress                    │
│    • Triggers site rebuild when complete        │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│ 4. QA Report                                     │
│    .github/workflows/qa_report.yml              │
│    • Generates QA statistics                    │
│    • Creates GitHub issue if pass rate < 90%    │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│ 5. Site Rebuild                                  │
│    .github/workflows/build.yml                  │
│    • Skips harvest/translation                  │
│    • Renders site from data/translated/         │
│    • Deploys to Cloudflare Pages                │
└──────────────────────────────────────────────────┘
```

## Setup Instructions

### 1. Initialize the Queue (One-time)

```bash
# Initialize queue with all papers (IA + ChinaXiv)
python scripts/init_cloud_queue.py

# Review queue stats
python -m src.cloud_job_queue stats

# Commit queue file
git add data/cloud_jobs.json
git commit -m "feat: initialize cloud job queue with 33,645 papers"
git push
```

**Options:**
- `--ia-only`: Only include Internet Archive papers
- `--chinaxiv-only`: Only include ChinaXiv papers
- `--limit 100`: Limit for testing
- `--force`: Re-initialize existing queue

### 2. Test with Small Batch (Recommended)

```bash
# Test locally with 10 papers
python -m src.pipeline \
  --cloud-mode \
  --with-qa \
  --batch-size 10 \
  --workers 5 \
  --worker-id "test-local"

# Check results
ls data/translated/  # Should have ~9-10 files
ls data/flagged/     # May have 0-1 files

# Review queue
python -m src.cloud_job_queue stats
```

### 3. Run Single Batch (GitHub Actions)

Navigate to: **Actions → Batch Translation Worker → Run workflow**

**Inputs:**
- **batch_size**: `500` (papers per run)
- **workers**: `80` (parallel workers)
- **runner_type**: `ubuntu-latest-8-cores`

**Expected time**: ~2-3 hours for 500 papers

### 4. Run Full Translation (Orchestrator)

Navigate to: **Actions → Translation Orchestrator → Run workflow**

**Inputs:**
- **total_batches**: `0` (run until queue empty) or `70` (for ~35k papers)
- **batch_size**: `500`
- **workers**: `80`
- **runner_type**: `ubuntu-latest-8-cores`
- **delay_between_batches**: `60` (seconds)

**Expected time**:
- Sequential: ~7 days (67 batches × 2.5 hours)
- With manual parallelization: ~1.5 days (run 5 batches simultaneously)

## Performance Estimates

### With 8-core Runner (ubuntu-latest-8-cores)

| Papers | Workers | Time/Batch | Total Time (Sequential) |
|--------|---------|------------|------------------------|
| 500    | 80      | ~2.5 hrs   | -                      |
| 5,000  | 80      | ~25 hrs    | 1 day                  |
| 10,000 | 80      | ~50 hrs    | 2 days                 |
| 33,645 | 80      | ~168 hrs   | 7 days                 |

**Speedup with parallel batches:**
- 3x parallel: 7 days → 2.3 days
- 5x parallel: 7 days → 1.4 days

## Monitoring Progress

### Queue Statistics

```bash
# View queue stats
python -m src.cloud_job_queue stats

# View failed jobs
python -m src.cloud_job_queue failed

# View QA-flagged jobs
python -m src.cloud_job_queue qa-flagged

# Reset stuck jobs (if workflow crashed)
python -m src.cloud_job_queue reset-stuck --timeout 60
```

### GitHub Actions

- **Workflow runs**: Check Actions tab for status
- **Artifacts**: Download flagged translations from completed runs
- **Logs**: View detailed translation logs in workflow output

### QA Reports

QA reports are generated daily and after orchestrator completes:
- Uploaded as artifacts in QA Report workflow
- GitHub issue created if pass rate < 90%

## Troubleshooting

### Queue is Empty but Papers Not Translated

```bash
# Reset failed jobs and retry
python -m src.cloud_job_queue retry
```

### Workflow Stuck/Crashed

```bash
# Reset in_progress jobs back to pending
python -m src.cloud_job_queue reset-stuck --timeout 60

# Push update
git add data/cloud_jobs.json
git commit -m "fix: reset stuck jobs"
git push
```

### Low QA Pass Rate

1. Check flagged translations: Download artifacts from workflow runs
2. Review `data/flagged/*.json` files for common patterns
3. Adjust QA thresholds in `src/qa_filter.py` if needed
4. Re-translate flagged papers:
   ```bash
   python -m src.cloud_job_queue reset-failed
   ```

### Git Push Conflicts

The batch workflow has retry logic (5 attempts). If conflicts persist:
1. Manually pull latest: `git pull origin main --rebase`
2. Re-run the batch workflow

## Advanced Usage

### Parallel Batch Execution

To speed up translation, manually trigger multiple `translate_batch.yml` workflows simultaneously:

1. Go to Actions → Batch Translation Worker
2. Click "Run workflow"
3. Repeat 3-5 times immediately

Each workflow will claim a different batch from the queue. Monitor to ensure they don't conflict.

### Custom Batch Sizes

Adjust based on runner type:
- **2-core runner**: batch_size=200, workers=40
- **4-core runner**: batch_size=300, workers=60
- **8-core runner**: batch_size=500, workers=80
- **16-core runner**: batch_size=1000, workers=160

### QA Threshold Tuning

Edit `src/qa_filter.py`:

```python
# Current thresholds
self.MAX_CHINESE_IDEOGRAPH_RATIO = 0.001  # 0.1% max Chinese
self.MAX_CHINESE_PUNCTUATION_RATIO = 0.01  # 1% max Chinese punctuation
self.MIN_ABSTRACT_LENGTH = 50  # Min chars in abstract
```

Adjust if getting too many false positives/negatives.

## File Structure

```
data/
├── cloud_jobs.json          # Job queue (Git-tracked)
├── translated/              # QA-approved translations
├── flagged/                 # QA-flagged translations (for review)
└── records/                 # Source papers (IA + ChinaXiv)

src/
├── cloud_job_queue.py       # Cloud-native job queue
├── qa_filter.py             # QA filtering system
└── pipeline.py              # Translation pipeline

.github/workflows/
├── translate_batch.yml      # Batch worker
├── translate_orchestrator.yml  # Orchestrator
├── qa_report.yml            # QA reporting
└── build.yml                # Site rebuild

scripts/
└── init_cloud_queue.py      # Queue initialization
```

## Next Steps After Translation Complete

1. **Review QA Report**: Check pass rate and flagged translations
2. **Trigger Site Rebuild**:
   ```bash
   # Via GitHub CLI
   gh workflow run build.yml -f skip_harvest=true

   # Or via Actions UI
   # Actions → build-and-deploy → Run workflow
   # ✓ Skip harvest and translation
   ```
3. **Deploy**: Site automatically deploys to Cloudflare Pages
4. **Monitor**: Check deployment success and site functionality

## Support

- **Issues**: Create GitHub issue for bugs or questions
- **Queue management**: Use `python -m src.cloud_job_queue --help`
- **Logs**: Check workflow logs in GitHub Actions tab
- **QA**: Review `data/qa_report.md` for translation quality metrics
