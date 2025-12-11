#!/usr/bin/env python3
"""Generate gap analysis report for 2025 core ML/DL papers."""
from collections import Counter

# Load the filtered paper IDs
with open('data/core_ml_dl_2025_papers.txt') as f:
    core_ml_dl_ids = [line.strip() for line in f]

# Count by month
months = Counter()
for paper_id in core_ml_dl_ids:
    month = paper_id.split('-')[1][:6]
    months[month] += 1

# Generate report
print('=' * 70)
print('2025 CORE ML/DL PAPERS - GAP ANALYSIS REPORT')
print('=' * 70)
print()
print('SUMMARY')
print('-' * 70)
print('Total 2025 papers with text translations: 1,291')
print('Core ML/DL papers (strict filter):        116 (9.0%)')
print('Current papers with figures:               89 (all years)')
print('Estimated 2025 papers with figures:        ~10-15')
print('Gap (need figure translation):             ~100-106')
print()

print('BREAKDOWN BY MONTH (Most Recent First)')
print('-' * 70)
print('Month    Core ML/DL Papers')
print('-' * 70)
for month in sorted(months.keys(), reverse=True):
    print(f'{month}   {months[month]:3d}')
print('-' * 70)
print(f'Total    {sum(months.values()):3d}')
print()

print('COST ESTIMATES (for figure translation)')
print('-' * 70)
print('Assuming average 5 figures per paper:')
print('  116 papers × 5 figures = 580 figures')
print('  580 figures × $0.08 = $46.40')
print()
print('Budget-optimized (within $50):')
print('  Target: ~115-120 papers')
print('  Available budget: $50')
print('  All 116 papers FIT within budget!')
print()

print('PRIORITIZATION FOR PILOT RUN')
print('-' * 70)
print('Recommended approach: Start with most recent month')
print()
print('Pilot Batch (December 2025):')
print('  Month:    202512')
print(f'  Papers:   {months.get("202512", 0)}')
print(f'  Figures:  ~{months.get("202512", 0) * 5} (assuming 5/paper)')
print(f'  Cost:     ~${months.get("202512", 0) * 5 * 0.08:.2f}')
print()
print('Scale-up Batches:')
for i, month in enumerate(sorted(months.keys(), reverse=True)[1:4], start=2):
    count = months[month]
    print(f'  Batch {i}: {month} - {count} papers (~${count * 5 * 0.08:.2f})')
print()

print('NEXT STEPS')
print('-' * 70)
print('1. Run pilot: gh workflow run figure-backfill.yml -f month=202512 -f limit=25')
print('2. Monitor cost and quality')
print('3. Scale up to remaining months if pilot successful')
print('4. Deploy: gh workflow run deploy.yml')
print()
print('Paper ID list saved to: data/core_ml_dl_2025_papers.txt')
print('=' * 70)
