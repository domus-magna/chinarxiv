# ChinaRxiv API Cost Reference

Last updated: November 2025

## Text Translation (OpenRouter / DeepSeek)

| Item | Cost |
|------|------|
| Model | DeepSeek V3.2-Exp via OpenRouter |
| Cost per paper | ~$0.08 (title + abstract) |
| Historical spend | ~$300 for 3,872 papers |

## Figure Translation (Gemini 3 Pro Image)

| Item | Cost |
|------|------|
| Model | `gemini-3-pro-image-preview` via Google AI Studio |
| Cost per image | ~$0.039 (standard resolution up to 1024x1024) |
| Pricing basis | $30/1M output tokens, ~1290 tokens per output image |
| QA validation | Moondream API ~$0.001/image |

### Per-Figure Breakdown

| Component | Cost |
|-----------|------|
| Gemini translation | $0.039 |
| Moondream QA | $0.001 |
| **Total (1 pass)** | **~$0.04** |
| **With retry (3 passes)** | **~$0.12** |

### Scale Estimates

Assuming ~5 figures/paper average:

| Scale | Figures | Est. Cost |
|-------|---------|-----------|
| 100 papers | ~500 | $20-25 |
| 1,000 papers | ~5,000 | $200-250 |
| Full corpus (4,000 papers) | ~20,000 | $800-1,000 |

### Performance Metrics

From November 2025 test batch:
- 17 figures across 3 papers
- ~17 minutes total processing time
- ~1 minute per figure average
- Most figures translate in 1 pass; some require up to 3 passes

## Sources

- [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini 3 Pro Costs Guide](https://www.glbgpt.com/hub/gemini-3-pro-costs-gemini-3-api-costs-latest-insights-for-2025/)
