"""
Database connection wrapper for PostgreSQL.

This module provides a connection pooling wrapper for PostgreSQL operations.
Designed for production deployments with Railway managed PostgreSQL.

Environment requirements:
- DATABASE_URL: PostgreSQL connection string (required)
"""

import os
import logging
from typing import Optional

import psycopg2
from psycopg2 import pool  # noqa: F401 - used via psycopg2.pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    PostgreSQL connection pooling wrapper.

    Provides connection pool management for production-grade performance
    with Railway managed PostgreSQL or local PostgreSQL instances.
    """

    def __init__(self):
        """
        Initialize PostgreSQL connection pool.

        Reads DATABASE_URL from environment and creates a connection pool
        for efficient database access.

        Raises:
            ValueError: If DATABASE_URL not provided
            Exception: If connection pool creation fails
        """
        self._pool = None

        # Get DATABASE_URL from environment
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            raise ValueError(
                "DATABASE_URL environment variable required for PostgreSQL. "
                "See CLAUDE.md for setup instructions (Docker or Homebrew)."
            )

        # Initialize connection pool (production-grade)
        try:
            self._pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=20,  # Adjust based on expected concurrent requests
                dsn=database_url
            )
            logger.info("PostgreSQL connection pool initialized (1-20 connections)")
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL connection pool: {e}")
            raise

    def get_connection(self):
        """
        Get database connection from pool.

        Returns:
            psycopg2.connection: PostgreSQL connection with RealDictCursor support

        Raises:
            Exception: If connection cannot be retrieved from pool
        """
        try:
            conn = self._pool.getconn()
            return conn
        except Exception as e:
            logger.error(f"Failed to get connection from pool: {e}")
            raise

    def release_connection(self, conn):
        """
        Release connection back to pool.

        Args:
            conn: PostgreSQL connection to release
        """
        if conn is None:
            return

        try:
            self._pool.putconn(conn)
        except Exception as e:
            logger.error(f"Failed to return connection to pool: {e}")

    def get_cursor(self, conn):
        """
        Get cursor with RealDictCursor factory (dict-like row access).

        Args:
            conn: PostgreSQL database connection

        Returns:
            psycopg2.cursor: Cursor with RealDictCursor factory

        Example:
            cursor = adapter.get_cursor(conn)
            cursor.execute("SELECT * FROM papers WHERE id = %s", (paper_id,))
            paper = cursor.fetchone()
            # Access columns as dict: paper['title_en']
        """
        return conn.cursor(cursor_factory=RealDictCursor)

    def close(self):
        """
        Close all database connections and clean up pool.

        This should be called when shutting down the application.
        """
        if self._pool:
            try:
                self._pool.closeall()
                logger.info("PostgreSQL connection pool closed")
            except Exception as e:
                logger.error(f"Failed to close PostgreSQL connection pool: {e}")


# Global adapter instance (initialized by create_app in __init__.py)
_adapter: Optional[DatabaseAdapter] = None


def init_adapter() -> None:
    """
    Initialize global database adapter instance.

    This is now a no-op for lazy initialization. The actual database connection
    is created on first get_adapter() call. This allows the app to start even
    if the database is temporarily unavailable, enabling health checks and
    graceful degradation.

    Called by create_app() for backwards compatibility.
    """
    # No-op: Connection pool is created lazily on first get_adapter() call
    logger.info("Database adapter configured for lazy initialization")


def get_adapter() -> DatabaseAdapter:
    """
    Get global database adapter instance, creating it on first use.

    Uses lazy initialization - the connection pool is created when first needed,
    not at app startup. This allows the app to start even if the database is
    temporarily unavailable.

    Returns:
        DatabaseAdapter instance

    Raises:
        RuntimeError: If database connection cannot be established
    """
    global _adapter
    if _adapter is None:
        try:
            _adapter = DatabaseAdapter()
            logger.info("PostgreSQL connection pool created (lazy init)")
        except Exception as e:
            logger.error(f"Failed to create database adapter: {e}")
            raise RuntimeError(f"Database unavailable: {e}") from e
    return _adapter
