#!/usr/bin/env python3
"""Populate fixture data for validation gates when real artifacts are absent."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARVEST_FIXTURE = REPO_ROOT / "tests/fixtures/harvest/records_sample.json"
TRANSLATION_FIXTURE = REPO_ROOT / "tests/fixtures/translation/sample_translation.json"


def copy_fixture(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)


def generate_scanned_pdf(dest: Path, *, min_chars: int = 2000) -> None:
    """Generate a synthetic scanned PDF so OCR has sufficient characters."""
    from PIL import Image, ImageDraw, ImageFont

    dest.parent.mkdir(parents=True, exist_ok=True)

    base_text = (
        "Scanned OCR benchmark text for evaluation. "
        "This synthetic paragraph is repeated to guarantee more than the minimum "
        "character threshold required by the OCR validation gate. "
    )
    repeated = base_text * ((min_chars // len(base_text)) + 2)
    wrapped = textwrap.wrap(repeated, width=70)

    width, height = 1700, 2200
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    line_height = font.getbbox("A")[3] + 10
    y = 120
    for line in wrapped:
        draw.text((100, y), line, fill="black", font=font)
        y += line_height
        if y > height - 120:
            break

    img.save(dest, "PDF", resolution=200.0)


def ensure_harvest_fixtures() -> bool:
    dest_dir = REPO_ROOT / "data/records"
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = list(dest_dir.glob("*.json"))
    if existing:
        return False

    copy_fixture(HARVEST_FIXTURE, dest_dir / HARVEST_FIXTURE.name)

    pdf_dest = REPO_ROOT / "data/pdfs/sample.pdf"
    generate_scanned_pdf(pdf_dest)
    return True


def ensure_translation_fixtures() -> bool:
    dest_dir = REPO_ROOT / "data/translated"
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = list(dest_dir.glob("*.json"))
    if existing:
        return False

    copy_fixture(TRANSLATION_FIXTURE, dest_dir / TRANSLATION_FIXTURE.name)
    return True


def main() -> None:
    harvest_created = ensure_harvest_fixtures()
    translation_created = ensure_translation_fixtures()

    if harvest_created:
        print("Seeded harvest fixtures for validation gate")
    if translation_created:
        print("Seeded translation fixtures for validation gate")
    if not harvest_created and not translation_created:
        print("Existing data detected; no fixtures copied")


if __name__ == "__main__":
    main()
