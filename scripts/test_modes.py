"""Direct comparison of translation modes."""

import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.translation_service import TranslationService
from src.config import get_config
from src.body_extract import extract_body_paragraphs

paper_id = "chinaxiv-202511.00010"

# Load config
config = get_config()
service = TranslationService(config)

# Load paper
selected_path = Path("data/selected.json")
selected = json.loads(selected_path.read_text())
paper = next((p for p in selected if p["id"] == paper_id), None)

# Extract paragraphs
pdf_path = Path(f"data/pdfs/{paper_id}.pdf")
print(f"📄 Extracting from {pdf_path}...")
paragraphs = extract_body_paragraphs({"files": {"pdf_path": str(pdf_path)}})
print(f"✓ Extracted {len(paragraphs)} paragraphs")
print()

# Test with first 10 paragraphs
test_paras = paragraphs[:10]
print(f"Testing first {len(test_paras)} paragraphs...")
print()

# Per-paragraph mode
print("TEST 1: PER-PARAGRAPH MODE")
config["translation"]["whole_paper_mode"] = False
start = time.time()
perpara = service.translate_paragraphs(test_paras)
perpara_time = time.time() - start
print(f"✓ {perpara_time:.2f}s, {len(test_paras)} API calls")
print()

# Whole-paper mode
print("TEST 2: WHOLE-PAPER MODE")
config["translation"]["whole_paper_mode"] = True
start = time.time()
whole = service.translate_paragraphs(test_paras)
whole_time = time.time() - start
print(f"✓ {whole_time:.2f}s, 1 API call")
print()

print(f"SPEEDUP: {perpara_time / whole_time:.1f}x faster")
print(f"API REDUCTION: {len(test_paras)} → 1 ({(1 - 1/len(test_paras)) * 100:.0f}%)")
