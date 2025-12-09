#!/usr/bin/env python3
"""
Cleanup script to delete translations that don't have corresponding PDFs or are QA-flagged.

This script:
1. Lists all PDFs in B2 (s3://chinaxiv/pdfs/)
2. Lists all translations in B2 (s3://chinaxiv/validated/translations/)
3. Identifies translations to delete:
   - Translations WITHOUT corresponding PDFs
   - Translations that are QA-flagged (even if they have PDFs)
4. Generates deletion report
5. Optionally archives and deletes the translations

Usage:
    # Dry run (show what would be deleted)
    python scripts/cleanup_translations_without_pdfs.py --dry-run

    # Actually delete after confirmation
    python scripts/cleanup_translations_without_pdfs.py --confirm
"""

import argparse
import boto3
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Any
from collections import defaultdict


def get_s3_client():
    """Create and return S3 client for Backblaze B2."""
    return boto3.client(
        's3',
        endpoint_url=os.environ['BACKBLAZE_S3_ENDPOINT'],
        aws_access_key_id=os.environ['BACKBLAZE_KEY_ID'],
        aws_secret_access_key=os.environ['BACKBLAZE_APPLICATION_KEY']
    )


def list_pdfs(s3, bucket: str) -> Set[str]:
    """List all PDF paper IDs in B2."""
    print("📄 Listing PDFs in B2...")

    pdf_ids = set()
    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket, Prefix='pdfs/')

    for page in pages:
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('.pdf'):
                # Extract paper ID from path: pdfs/chinaxiv-202201.00001.pdf
                filename = Path(key).name
                paper_id = filename.replace('.pdf', '')
                pdf_ids.add(paper_id)

    print(f"   Found {len(pdf_ids)} PDFs in B2")
    return pdf_ids


def list_translations(s3, bucket: str) -> Dict[str, Any]:
    """List all translations in B2 with their metadata."""
    print("📝 Listing translations in B2...")

    translations = {}
    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket, Prefix='validated/translations/')

    count = 0
    for page in pages:
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('.json'):
                # Extract paper ID from path: validated/translations/chinaxiv-202201.00001.json
                filename = Path(key).name
                paper_id = filename.replace('.json', '')

                # Download translation to check QA status
                try:
                    response = s3.get_object(Bucket=bucket, Key=key)
                    data = json.loads(response['Body'].read())
                    qa_status = data.get('_qa_status', 'pass')

                    translations[paper_id] = {
                        'key': key,
                        'qa_status': qa_status,
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat()
                    }
                    count += 1
                    if count % 500 == 0:
                        print(f"   Processed {count} translations...")
                except Exception as e:
                    print(f"   ⚠️  Error reading {key}: {e}")

    print(f"   Found {len(translations)} translations in B2")
    return translations


def identify_translations_to_delete(
    translations: Dict[str, Any],
    pdf_ids: Set[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Identify translations to delete based on PDF existence and QA status."""
    print("\n🔍 Identifying translations to delete...")

    to_delete = {
        'no_pdf': [],
        'qa_flagged_with_pdf': [],
        'qa_flagged_no_pdf': []
    }

    for paper_id, metadata in translations.items():
        has_pdf = paper_id in pdf_ids
        qa_status = metadata['qa_status']
        is_flagged = qa_status != 'pass'

        if not has_pdf and is_flagged:
            to_delete['qa_flagged_no_pdf'].append({
                'paper_id': paper_id,
                'reason': f'QA flagged ({qa_status}) AND no PDF',
                **metadata
            })
        elif not has_pdf:
            to_delete['no_pdf'].append({
                'paper_id': paper_id,
                'reason': 'No corresponding PDF',
                **metadata
            })
        elif is_flagged:
            to_delete['qa_flagged_with_pdf'].append({
                'paper_id': paper_id,
                'reason': f'QA flagged ({qa_status})',
                **metadata
            })

    total = sum(len(v) for v in to_delete.values())
    print(f"\n   Translations to delete: {total}")
    print(f"      - No PDF: {len(to_delete['no_pdf'])}")
    print(f"      - QA flagged (with PDF): {len(to_delete['qa_flagged_with_pdf'])}")
    print(f"      - QA flagged (no PDF): {len(to_delete['qa_flagged_no_pdf'])}")

    return to_delete


def generate_report(
    to_delete: Dict[str, List[Dict[str, Any]]],
    pdf_count: int,
    translation_count: int
) -> Dict[str, Any]:
    """Generate deletion report."""
    total_to_delete = sum(len(v) for v in to_delete.values())
    remaining = translation_count - total_to_delete

    # Count QA status distribution
    qa_distribution = defaultdict(int)
    for category in to_delete.values():
        for item in category:
            qa_distribution[item['qa_status']] += 1

    report = {
        'generated_at': datetime.utcnow().isoformat(),
        'summary': {
            'total_pdfs': pdf_count,
            'total_translations_before': translation_count,
            'total_to_delete': total_to_delete,
            'total_remaining_after': remaining,
            'qa_status_of_deleted': dict(qa_distribution)
        },
        'categories': {
            'no_pdf': {
                'count': len(to_delete['no_pdf']),
                'items': to_delete['no_pdf'][:100]  # Sample first 100
            },
            'qa_flagged_with_pdf': {
                'count': len(to_delete['qa_flagged_with_pdf']),
                'items': to_delete['qa_flagged_with_pdf'][:100]
            },
            'qa_flagged_no_pdf': {
                'count': len(to_delete['qa_flagged_no_pdf']),
                'items': to_delete['qa_flagged_no_pdf'][:100]
            }
        }
    }

    return report


def save_report(report: Dict[str, Any], output_path: str):
    """Save deletion report to file."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\n📊 Report saved to: {output_path}")


def delete_translations(
    s3,
    bucket: str,
    to_delete: Dict[str, List[Dict[str, Any]]],
    archive: bool = True
) -> Dict[str, int]:
    """Delete translations from B2, optionally archiving them first."""
    print("\n🗑️  Deleting translations from B2...")

    deleted_count = 0
    archived_count = 0
    failed = []

    all_items = []
    for category in to_delete.values():
        all_items.extend(category)

    total = len(all_items)

    for i, item in enumerate(all_items, 1):
        key = item['key']
        paper_id = item['paper_id']

        try:
            # Archive first if requested
            if archive:
                archive_key = f"archive/deleted_translations/{datetime.utcnow().strftime('%Y-%m-%d')}/{Path(key).name}"
                s3.copy_object(
                    Bucket=bucket,
                    CopySource={'Bucket': bucket, 'Key': key},
                    Key=archive_key
                )
                archived_count += 1

            # Delete original
            s3.delete_object(Bucket=bucket, Key=key)
            deleted_count += 1

            if i % 100 == 0:
                print(f"   Deleted {i}/{total} translations...")

        except Exception as e:
            print(f"   ⚠️  Error deleting {paper_id}: {e}")
            failed.append({'paper_id': paper_id, 'error': str(e)})

    print(f"\n✅ Deletion complete:")
    print(f"   - Deleted: {deleted_count}")
    if archive:
        print(f"   - Archived: {archived_count}")
    if failed:
        print(f"   - Failed: {len(failed)}")

    return {
        'deleted': deleted_count,
        'archived': archived_count,
        'failed': len(failed),
        'failed_items': failed
    }


def main():
    parser = argparse.ArgumentParser(
        description="Cleanup translations without PDFs or QA-flagged translations"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )
    parser.add_argument(
        '--confirm',
        action='store_true',
        help='Actually delete translations (requires explicit confirmation)'
    )
    parser.add_argument(
        '--no-archive',
        action='store_true',
        help='Skip archiving deleted translations to B2 (not recommended)'
    )
    parser.add_argument(
        '--output',
        default='reports/translations_to_delete.json',
        help='Path to save deletion report'
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.dry_run and not args.confirm:
        parser.error("Must specify either --dry-run or --confirm")

    if args.confirm and args.dry_run:
        parser.error("Cannot specify both --confirm and --dry-run")

    # Check environment variables
    required_env = ['BACKBLAZE_S3_ENDPOINT', 'BACKBLAZE_KEY_ID', 'BACKBLAZE_APPLICATION_KEY', 'BACKBLAZE_BUCKET']
    missing = [var for var in required_env if not os.environ.get(var)]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        return 1

    bucket = os.environ['BACKBLAZE_BUCKET']
    s3 = get_s3_client()

    print("=" * 80)
    print("ChinaRxiv Translation Cleanup")
    print("=" * 80)

    # Step 1: List PDFs
    pdf_ids = list_pdfs(s3, bucket)

    # Step 2: List translations
    translations = list_translations(s3, bucket)

    # Step 3: Identify translations to delete
    to_delete = identify_translations_to_delete(translations, pdf_ids)

    # Step 4: Generate report
    report = generate_report(to_delete, len(pdf_ids), len(translations))
    save_report(report, args.output)

    # Step 5: Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total PDFs in B2:           {len(pdf_ids)}")
    print(f"Total translations in B2:   {len(translations)}")
    print(f"Translations to delete:     {report['summary']['total_to_delete']}")
    print(f"Translations to keep:       {report['summary']['total_remaining_after']}")
    print("\nQA status of deleted translations:")
    for status, count in report['summary']['qa_status_of_deleted'].items():
        print(f"   {status}: {count}")

    # Step 6: Execute deletion if confirmed
    if args.confirm:
        print("\n" + "=" * 80)
        print("⚠️  WARNING: This will permanently delete translations from B2!")
        print("=" * 80)
        confirmation = input("\nType 'DELETE' to confirm: ")

        if confirmation != 'DELETE':
            print("❌ Deletion cancelled")
            return 0

        deletion_result = delete_translations(
            s3,
            bucket,
            to_delete,
            archive=not args.no_archive
        )

        # Save deletion log
        log_path = 'reports/deletion_log.json'
        deletion_log = {
            'timestamp': datetime.utcnow().isoformat(),
            'dry_run': False,
            'archived': not args.no_archive,
            **deletion_result
        }
        save_report(deletion_log, log_path)
        print(f"📝 Deletion log saved to: {log_path}")

    else:
        print("\n" + "=" * 80)
        print("DRY RUN MODE - No deletions performed")
        print("=" * 80)
        print(f"\nTo actually delete, run with --confirm flag:")
        print(f"  python {__file__} --confirm")

    return 0


if __name__ == '__main__':
    exit(main())
