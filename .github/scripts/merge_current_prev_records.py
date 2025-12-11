#!/usr/bin/env python3
"""Merge current and previous month's ChinaXiv records into a single file."""

import os
import json

os.makedirs('data/records', exist_ok=True)
curr = os.popen("date -u +%Y%m").read().strip()
try:
    prev = os.popen("date -u -d '-1 month' +%Y%m").read().strip()
except Exception:
    prev = ''

paths = []
for ym in (curr, prev):
    if not ym:
        continue
    p = f"data/records/chinaxiv_{ym}.json"
    if os.path.exists(p):
        paths.append(p)

merged = []
for p in paths:
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                merged.extend(data)
    except Exception:
        pass

outp = 'data/records/_merged_current_prev.json'
with open(outp, 'w', encoding='utf-8') as f:
    json.dump(merged, f, ensure_ascii=False)

print(f"Merged {len(paths)} record files → {outp} with {len(merged)} items")
