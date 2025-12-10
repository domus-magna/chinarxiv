"""
Pytest configuration and fixtures for ChinaRxiv English test suite.

This module provides reusable fixtures for:
- PostgreSQL test database setup with isolation
- Flask app factory with test configuration
- Test client for route testing
- Sample test data

Requires:
- Local PostgreSQL server (Docker or Homebrew)
- TEST_DATABASE_URL environment variable (optional, defaults to localhost)
"""

import sys
from pathlib import Path
import pytest
import psycopg2
import json
import os

# Ensure project root is on sys.path to import src.* modules
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402


@pytest.fixture(scope='session')
def test_database_url():
    """
    Get PostgreSQL test database URL from environment or use local default.

    Override with: TEST_DATABASE_URL='postgresql://user:pass@host/dbname'
    Default: postgresql://localhost/chinaxiv_test
    """
    return os.environ.get('TEST_DATABASE_URL', 'postgresql://localhost/chinaxiv_test')


@pytest.fixture(scope='session')
def test_database_schema(test_database_url):
    """
    Create test database schema using migration script functions.

    This fixture runs once per test session and sets up:
    - papers table
    - paper_subjects table
    - Full-text search (tsvector generated column)
    - All indexes
    - Materialized views

    Cleanup: Drops test database at end of session (optional)
    """
    from scripts.migrate_to_postgres import create_postgres_schema, create_materialized_views

    conn = psycopg2.connect(test_database_url)

    # Create schema
    create_postgres_schema(conn)
    create_materialized_views(conn)

    conn.close()

    yield test_database_url

    # Cleanup (optional): Drop all tables at end of session
    # Uncomment if you want clean slate each session
    # conn = psycopg2.connect(test_database_url)
    # cursor = conn.cursor()
    # cursor.execute("DROP SCHEMA public CASCADE;")
    # cursor.execute("CREATE SCHEMA public;")
    # conn.commit()
    # conn.close()


@pytest.fixture
def test_database(test_database_schema):
    """
    Provide a clean database connection for each test.

    This fixture:
    - Uses the session-scoped schema
    - Clears all data before each test
    - Returns the database URL
    """
    conn = psycopg2.connect(test_database_schema)
    cursor = conn.cursor()

    # Clear all data (but keep schema)
    cursor.execute("DELETE FROM paper_subjects;")
    cursor.execute("DELETE FROM papers;")

    conn.commit()
    conn.close()

    return test_database_schema


@pytest.fixture
def app(test_database):
    """
    Create Flask app with test configuration.

    Returns:
        Flask app instance configured for testing
    """
    # Set DATABASE_URL environment variable for app initialization
    os.environ['DATABASE_URL'] = test_database

    app = create_app()
    app.config['TESTING'] = True
    app.config['PER_PAGE'] = 50

    yield app

    # Cleanup: unset DATABASE_URL
    if 'DATABASE_URL' in os.environ:
        del os.environ['DATABASE_URL']


@pytest.fixture
def client(app):
    """
    Create Flask test client for route testing.

    Returns:
        Flask test client
    """
    return app.test_client()


@pytest.fixture
def sample_papers(test_database):
    """
    Insert sample papers into test database for testing.

    Creates a diverse set of papers covering:
    - Different subjects (Computer Science, Biology, Physics, etc.)
    - Different dates (2020-2024)
    - Papers with and without figures
    - Different QA statuses
    - Unicode in titles/abstracts
    """
    conn = psycopg2.connect(test_database)
    cursor = conn.cursor()

    papers = [
        # Computer Science - AI papers (2022)
        {
            'id': 'chinaxiv-202201.00001',
            'title_en': 'Deep Learning for Neural Network Optimization',
            'abstract_en': 'This paper presents a novel approach to optimize neural networks using deep learning techniques.',
            'creators_en': json.dumps(['Zhang Wei', 'Li Ming']),
            'date': '2022-01-15T10:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Computer Science', 'Artificial Intelligence']
        },
        {
            'id': 'chinaxiv-202206.00002',
            'title_en': 'Transformer Models for Natural Language Processing',
            'abstract_en': 'We explore transformer architectures for NLP tasks and achieve state-of-the-art results.',
            'creators_en': json.dumps(['Wang Hua', 'Liu Jun']),
            'date': '2022-06-20T14:30:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Computer Science', 'Natural Language Processing']
        },
        {
            'id': 'chinaxiv-202212.00003',
            'title_en': 'Graph Neural Networks for Social Network Analysis',
            'abstract_en': 'This study applies graph neural networks to analyze social network structures.',
            'creators_en': json.dumps(['Chen Xiao']),
            'date': '2022-12-31T23:59:59',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Computer Science', 'Graph Theory']
        },

        # Biology papers (2023)
        {
            'id': 'chinaxiv-202301.00004',
            'title_en': 'CRISPR Gene Editing in Plant Biology',
            'abstract_en': 'We demonstrate successful CRISPR-based gene editing in rice plants.',
            'creators_en': json.dumps(['Zhao Yu', 'Sun Lei']),
            'date': '2023-01-10T09:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Biology', 'Genetics']
        },
        {
            'id': 'chinaxiv-202307.00005',
            'title_en': 'Protein Folding Mechanisms in Cellular Stress',
            'abstract_en': 'This research investigates protein folding under various stress conditions.',
            'creators_en': json.dumps(['Wu Dan']),
            'date': '2023-07-15T12:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Biology', 'Biochemistry']
        },

        # Physics papers (2024)
        {
            'id': 'chinaxiv-202401.00006',
            'title_en': 'Quantum Computing with Superconducting Qubits',
            'abstract_en': 'We report progress in building scalable quantum computers using superconducting qubits.',
            'creators_en': json.dumps(['Huang Jie', 'Ma Qiang']),
            'date': '2024-01-05T08:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Physics', 'Quantum Computing']
        },
        {
            'id': 'chinaxiv-202408.00007',
            'title_en': 'Dark Matter Detection Using Novel Sensors',
            'abstract_en': 'This paper describes new sensor technology for detecting dark matter particles.',
            'creators_en': json.dumps(['Xu Lin']),
            'date': '2024-08-20T16:00:00',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Physics', 'Astrophysics']
        },

        # Paper with Unicode (Chinese characters in English text - testing XSS)
        {
            'id': 'chinaxiv-202410.00008',
            'title_en': 'Machine Learning Applications in 农业 (Agriculture)',
            'abstract_en': 'This paper explores ML applications in precision agriculture and 智能farming.',
            'creators_en': json.dumps(['Zheng Mei']),
            'date': '2024-10-15T11:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Computer Science', 'Agriculture']
        },

        # Flagged paper (QA failed)
        {
            'id': 'chinaxiv-202411.00009',
            'title_en': 'Incomplete Translation Example',
            'abstract_en': 'This translation has too many Chinese characters: 这是一个失败的翻译示例。',
            'creators_en': json.dumps(['Test Author']),
            'date': '2024-11-01T10:00:00',
            'has_figures': False,
            'has_full_text': False,
            'qa_status': 'fail',
            'subjects': ['Computer Science']
        },

        # Paper with special characters in title (testing XSS)
        {
            'id': 'chinaxiv-202412.00010',
            'title_en': 'Testing <script>alert("XSS")</script> Security',
            'abstract_en': 'This paper tests XSS prevention in the web application.',
            'creators_en': json.dumps(['Security Tester']),
            'date': '2024-12-01T10:00:00',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Computer Science']
        },

        # Old paper (2020)
        {
            'id': 'chinaxiv-202001.00011',
            'title_en': 'Early COVID-19 Research Findings',
            'abstract_en': 'Initial research on the novel coronavirus outbreak.',
            'creators_en': json.dumps(['Chen Wei', 'Li Hong']),
            'date': '2020-02-15T09:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'subjects': ['Biology', 'Virology']
        },
    ]

    # Insert papers and subjects
    for paper in papers:
        cursor.execute("""
        INSERT INTO papers (
            id, title_en, abstract_en, creators_en, date,
            has_figures, has_full_text, qa_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            paper['id'],
            paper['title_en'],
            paper['abstract_en'],
            paper['creators_en'],
            paper['date'],
            paper['has_figures'],
            paper['has_full_text'],
            paper['qa_status']
        ))

        # Insert subjects
        for subject in paper['subjects']:
            cursor.execute("""
            INSERT INTO paper_subjects (paper_id, subject)
            VALUES (%s, %s)
            """, (paper['id'], subject))

    # Refresh materialized view with test data
    cursor.execute("REFRESH MATERIALIZED VIEW category_counts;")

    conn.commit()
    conn.close()

    yield test_database


@pytest.fixture
def sample_category_taxonomy():
    """
    Provide sample category taxonomy for filter testing.

    Returns:
        dict: Category taxonomy structure
    """
    return {
        'ai_computing': {
            'label': 'AI & Computing',
            'order': 1,
            'subjects': ['Computer Science', 'Artificial Intelligence', 'Natural Language Processing']
        },
        'biology': {
            'label': 'Biology & Life Sciences',
            'order': 2,
            'subjects': ['Biology', 'Genetics', 'Biochemistry', 'Virology']
        },
        'physics': {
            'label': 'Physics & Astronomy',
            'order': 3,
            'subjects': ['Physics', 'Quantum Computing', 'Astrophysics']
        }
    }
