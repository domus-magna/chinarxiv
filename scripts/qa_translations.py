#!/usr/bin/env python3
"""
Quality Assurance script for translations.
Automatically filters out translations with Chinese characters and flags them for review.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from qa_filter import SynthesisQAFilter, QAStatus


def load_translations(translated_dir: str) -> List[Dict[str, Any]]:
    """Load all translations from directory."""
    translations = []
    translated_path = Path(translated_dir)
    
    for json_file in translated_path.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                translation = json.load(f)
                translation['_file_path'] = str(json_file)
                translations.append(translation)
        except Exception as e:
            print(f"Error loading {json_file}: {e}")
            continue
    
    return translations


def save_flagged_translations(flagged: List[Dict[str, Any]], output_dir: str) -> None:
    """Save flagged translations for review."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    for translation in flagged:
        paper_id = translation.get('id', 'unknown')
        output_file = output_path / f"{paper_id}_flagged.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(translation, f, indent=2, ensure_ascii=False)
        
        print(f"Flagged: {paper_id} -> {output_file}")


def remove_flagged_from_site(flagged: List[Dict[str, Any]], site_dir: str) -> None:
    """Remove flagged translations from site directory."""
    site_path = Path(site_dir)
    
    for translation in flagged:
        paper_id = translation.get('id', 'unknown')
        item_dir = site_path / "items" / paper_id
        
        if item_dir.exists():
            import shutil
            shutil.rmtree(item_dir)
            print(f"Removed from site: {item_dir}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="QA filter for translations")
    parser.add_argument("--translated-dir", default="data/translated", 
                       help="Directory containing translations")
    parser.add_argument("--site-dir", default="site", 
                       help="Site directory")
    parser.add_argument("--flagged-dir", default="data/flagged", 
                       help="Directory to save flagged translations")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Show what would be done without making changes")
    parser.add_argument("--execute", action="store_true", 
                       help="Actually remove flagged translations")
    parser.add_argument("--analyze-only", action="store_true", 
                       help="Only analyze, don't filter")
    
    args = parser.parse_args()
    
    if args.execute:
        args.dry_run = False
    
    print("Loading translations...")
    translations = load_translations(args.translated_dir)
    print(f"Loaded {len(translations)} translations")
    
    if args.analyze_only:
        # Just analyze without filtering
        qa_filter = SynthesisQAFilter()

        stats = {
            'total': len(translations),
            'pass': 0,
            'flag_chinese': 0,
            'flag_formatting': 0,
            'flag_length': 0,
            'flag_math': 0,
            'flag_content': 0
        }

        for translation in translations:
            result = qa_filter.check_synthesis_translation(translation)
            stats[result.status.value] += 1

            if result.status.value != 'pass':
                paper_id = translation.get('id', 'unknown')
                print(f"\n{paper_id}: {result.status.value}")
                print(f"  Score: {result.score:.2f}")
                print(f"  Chinese Ratio: {result.chinese_ratio:.2%}")
                print(f"  Chinese Chars: {result.chinese_chars}")
                print(f"  Issues: {result.issues}")
                print(f"  Flagged Fields: {result.flagged_fields}")

        print(f"\nAnalysis Summary:")
        for status, count in stats.items():
            print(f"  {status}: {count}")

        return

    # Filter translations using synthesis QA
    print("Filtering translations...")
    qa_filter = SynthesisQAFilter()
    passed = []
    flagged = []

    for translation in translations:
        result = qa_filter.check_synthesis_translation(translation)
        translation['_qa_status'] = result.status.value
        translation['_qa_score'] = result.score
        translation['_qa_issues'] = result.issues

        if result.status == QAStatus.PASS:
            passed.append(translation)
        else:
            flagged.append(translation)
    
    print(f"\nFiltering Results:")
    print(f"  Passed: {len(passed)}")
    print(f"  Flagged: {len(flagged)}")
    
    if flagged:
        print(f"\nFlagged translations:")
        for translation in flagged:
            paper_id = translation.get('id', 'unknown')
            qa_status = translation.get('_qa_status', 'unknown')
            qa_score = translation.get('_qa_score', 0.0)
            qa_issues = translation.get('_qa_issues', [])
            
            print(f"  {paper_id}: {qa_status} (score: {qa_score:.2f})")
            for issue in qa_issues[:2]:  # Show first 2 issues
                print(f"    - {issue}")
            if len(qa_issues) > 2:
                print(f"    - ... and {len(qa_issues) - 2} more issues")
        
        if not args.dry_run:
            # Save flagged translations for review
            print(f"\nSaving flagged translations to {args.flagged_dir}...")
            save_flagged_translations(flagged, args.flagged_dir)
            
            if args.execute:
                # Remove flagged translations from site
                print(f"Removing flagged translations from site...")
                remove_flagged_from_site(flagged, args.site_dir)
                
                # Remove flagged translation files
                for translation in flagged:
                    file_path = translation.get('_file_path')
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                        print(f"Removed: {file_path}")
                
                print("Flagged translations removed!")
            else:
                print("Dry run - no changes made. Use --execute to actually remove them.")
        else:
            print("Dry run - no changes made.")
    else:
        print("No translations flagged for review!")


if __name__ == "__main__":
    main()
