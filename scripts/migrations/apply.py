#!/usr/bin/env python3
"""
Apply SQL migrations to PostgreSQL database.

Usage:
    python scripts/migrations/apply.py
    python scripts/migrations/apply.py --dry-run
    python scripts/migrations/apply.py --migration 001_add_processing_status

Environment:
    DATABASE_URL: PostgreSQL connection string (required)
"""

import os
import sys
import argparse
import logging
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_applied_migrations(conn) -> set:
    """Get list of already-applied migrations."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT version FROM schema_migrations")
        return {row['version'] for row in cursor.fetchall()}
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet - no migrations applied
        return set()


def apply_migration(conn, migration_path: Path, dry_run: bool = False) -> bool:
    """Apply a single migration file."""
    migration_name = migration_path.stem

    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Applying migration: {migration_name}")

    # Read SQL
    sql = migration_path.read_text()

    if dry_run:
        logger.info(f"SQL to execute:\n{sql[:500]}...")
        return True

    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        conn.commit()
        logger.info(f"Successfully applied: {migration_name}")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to apply {migration_name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Apply SQL migrations')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--migration', type=str,
                        help='Apply specific migration only')
    args = parser.parse_args()

    # Check for DATABASE_URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)

    # Find migrations directory
    migrations_dir = Path(__file__).parent
    if not migrations_dir.exists():
        logger.error(f"Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    # Connect to PostgreSQL
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        logger.info("Connected to PostgreSQL")
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    # Get applied migrations
    applied = get_applied_migrations(conn)
    logger.info(f"Already applied: {len(applied)} migrations")

    # Get migration files (sorted by name)
    migration_files = sorted(migrations_dir.glob('*.sql'))

    if args.migration:
        # Filter to specific migration
        migration_files = [f for f in migration_files if args.migration in f.stem]
        if not migration_files:
            logger.error(f"Migration not found: {args.migration}")
            sys.exit(1)

    # Apply pending migrations
    applied_count = 0
    skipped_count = 0
    failed_count = 0

    for migration_path in migration_files:
        migration_name = migration_path.stem

        if migration_name in applied and not args.migration:
            logger.info(f"Skipping already applied: {migration_name}")
            skipped_count += 1
            continue

        if apply_migration(conn, migration_path, dry_run=args.dry_run):
            applied_count += 1
        else:
            failed_count += 1
            if not args.dry_run:
                logger.error("Stopping due to failed migration")
                break

    # Summary
    logger.info(f"\nMigration summary:")
    logger.info(f"  Applied: {applied_count}")
    logger.info(f"  Skipped: {skipped_count}")
    logger.info(f"  Failed: {failed_count}")

    conn.close()

    if failed_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
