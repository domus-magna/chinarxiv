"""
Database adapter for SQLite and PostgreSQL dual support.

This module provides a unified interface for database operations that abstracts
the differences between SQLite and PostgreSQL, including:
- Connection management (sqlite3 vs psycopg2 with connection pooling)
- Row factories (sqlite3.Row vs RealDictCursor)
- Parameter placeholders (? vs %s)
- Full-text search (FTS5 vs tsvector)
- Exception classes

Environment-based database detection:
- SQLite (default): No DATABASE_URL environment variable
- PostgreSQL (production): DATABASE_URL environment variable present
"""

import os
import logging
from typing import Optional, Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Database type detection from environment
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith('postgresql://')

# Import appropriate database modules based on detected type
if IS_POSTGRES:
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        from psycopg2 import pool
    except ImportError:
        logger.error("psycopg2 not installed but DATABASE_URL is set for PostgreSQL")
        raise
else:
    import sqlite3


class DatabaseAdapter:
    """
    Database adapter providing unified interface for SQLite and PostgreSQL.

    This adapter encapsulates database-specific logic to enable dual database
    support with minimal code changes to the application layer.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize database adapter.

        Args:
            config: Flask app configuration dictionary containing database settings
        """
        self.config = config
        self.is_postgres = IS_POSTGRES
        self._pool = None

        if self.is_postgres:
            # Connection pooling for PostgreSQL (production-grade)
            try:
                self._pool = psycopg2.pool.SimpleConnectionPool(
                    minconn=1,
                    maxconn=20,  # Adjust based on expected concurrent requests
                    dsn=DATABASE_URL
                )
                logger.info("PostgreSQL connection pool initialized (1-20 connections)")
            except Exception as e:
                logger.error(f"Failed to create PostgreSQL connection pool: {e}")
                raise
        else:
            logger.info("SQLite mode enabled (development/testing)")

    def get_connection(self):
        """
        Get database connection with appropriate row factory.

        For PostgreSQL: Gets connection from pool
        For SQLite: Creates new connection with Row factory

        Returns:
            Connection object (psycopg2.connection or sqlite3.Connection)
        """
        if self.is_postgres:
            try:
                conn = self._pool.getconn()
                return conn
            except Exception as e:
                logger.error(f"Failed to get connection from pool: {e}")
                raise
        else:
            try:
                conn = sqlite3.connect(self.config['DATABASE'])
                conn.row_factory = sqlite3.Row
                return conn
            except Exception as e:
                logger.error(f"Failed to connect to SQLite database: {e}")
                raise

    def release_connection(self, conn):
        """
        Release connection back to pool (PostgreSQL) or close (SQLite).

        Args:
            conn: Database connection to release
        """
        if conn is None:
            return

        if self.is_postgres:
            try:
                self._pool.putconn(conn)
            except Exception as e:
                logger.error(f"Failed to return connection to pool: {e}")
        else:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Failed to close SQLite connection: {e}")

    def get_cursor(self, conn):
        """
        Get cursor with appropriate row factory.

        For PostgreSQL: Returns cursor with RealDictCursor (dict-like rows)
        For SQLite: Returns standard cursor (sqlite3.Row already set on connection)

        Args:
            conn: Database connection

        Returns:
            Cursor object
        """
        if self.is_postgres:
            return conn.cursor(cursor_factory=RealDictCursor)
        else:
            return conn.cursor()

    def adapt_placeholder(self, query: str) -> str:
        """
        Convert SQLite ? placeholders to PostgreSQL %s placeholders.

        This is a simple string replacement that works for our parameterized queries.
        For more complex scenarios, consider using a query builder library.

        Args:
            query: SQL query string with ? placeholders

        Returns:
            SQL query string with %s placeholders (PostgreSQL) or unchanged (SQLite)
        """
        if self.is_postgres:
            return query.replace('?', '%s')
        return query

    def adapt_fts_query(self, search_term: str) -> Tuple[str, List[str]]:
        """
        Adapt full-text search query for database type.

        SQLite uses FTS5 virtual table with MATCH operator:
            - Separate papers_fts table with triggers for sync
            - MATCH operator for full-text queries

        PostgreSQL uses native tsvector with @@ operator:
            - Generated column search_vector (tsvector)
            - plainto_tsquery for user queries
            - GIN index for performance

        Args:
            search_term: User's search query string

        Returns:
            Tuple of (query_fragment, params_list)
            - query_fragment: SQL WHERE clause fragment
            - params_list: List of parameters for the query
        """
        if self.is_postgres:
            # PostgreSQL: use tsvector search with plainto_tsquery
            # plainto_tsquery converts text to tsquery (handles special chars gracefully)
            return (
                "p.search_vector @@ plainto_tsquery('english', %s)",
                [search_term]
            )
        else:
            # SQLite: use FTS5 virtual table
            # Subquery checks if paper ID exists in FTS5 results
            return (
                """p.id IN (
                    SELECT id FROM papers_fts
                    WHERE papers_fts MATCH ?
                )""",
                [search_term]
            )

    def get_exception_class(self):
        """
        Get the appropriate OperationalError exception class for this database.

        This allows database-agnostic exception handling in query code.

        Returns:
            Exception class for database operational errors
        """
        if self.is_postgres:
            return psycopg2.OperationalError
        else:
            return sqlite3.OperationalError

    def close(self):
        """
        Close all database connections and clean up resources.

        For PostgreSQL: Closes all connections in pool
        For SQLite: No-op (connections closed per-request)

        This should be called when shutting down the application.
        """
        if self.is_postgres and self._pool:
            try:
                self._pool.closeall()
                logger.info("PostgreSQL connection pool closed")
            except Exception as e:
                logger.error(f"Failed to close PostgreSQL connection pool: {e}")


# Global adapter instance (initialized by create_app in __init__.py)
_adapter: Optional[DatabaseAdapter] = None


def init_adapter(config: Dict[str, Any]) -> None:
    """
    Initialize global database adapter instance.

    This should be called once during application initialization (create_app).

    Args:
        config: Flask app configuration dictionary
    """
    global _adapter
    if _adapter is not None:
        logger.warning("Database adapter already initialized, reinitializing...")

    _adapter = DatabaseAdapter(config)
    logger.info(f"Database adapter initialized (type: {'PostgreSQL' if _adapter.is_postgres else 'SQLite'})")


def get_adapter() -> DatabaseAdapter:
    """
    Get global database adapter instance.

    Returns:
        DatabaseAdapter instance

    Raises:
        RuntimeError: If adapter not initialized (call init_adapter first)
    """
    if _adapter is None:
        raise RuntimeError(
            "Database adapter not initialized. "
            "Call init_adapter() in create_app() before using database."
        )
    return _adapter
