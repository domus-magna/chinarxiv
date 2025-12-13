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
from unittest.mock import patch

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
    Default: postgresql://postgres:password@localhost/chinaxiv_test (local dev)
    CI uses: postgresql://postgres:postgres@localhost:5432/chinaxiv_test
    """
    return os.environ.get(
        'TEST_DATABASE_URL',
        'postgresql://postgres:password@localhost/chinaxiv_test'
    )


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
    # Clear translation requests for isolation
    cursor.execute("DELETE FROM translation_requests;")
    # Clear user reports for isolation (new in v1 polish)
    cursor.execute("DELETE FROM user_reports;")

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
    - Both Chinese (_cn) and English (_en) columns
    """
    conn = psycopg2.connect(test_database)
    cursor = conn.cursor()

    papers = [
        # Computer Science - AI papers (2022)
        {
            'id': 'chinaxiv-202201.00001',
            'title_cn': '深度学习神经网络优化方法',
            'abstract_cn': '本文提出了一种利用深度学习技术优化神经网络的新方法。',
            'creators_cn': json.dumps(['张伟', '李明']),
            'subjects_cn': json.dumps(['计算机科学', '人工智能']),
            'title_en': 'Deep Learning for Neural Network Optimization',
            'abstract_en': 'This paper presents a novel approach to optimize neural networks using deep learning techniques.',
            'creators_en': json.dumps(['Zhang Wei', 'Li Ming']),
            'date': '2022-01-15T10:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Computer Science', 'Artificial Intelligence']
        },
        {
            'id': 'chinaxiv-202206.00002',
            'title_cn': 'Transformer模型在自然语言处理中的应用',
            'abstract_cn': '我们探索了用于NLP任务的transformer架构并取得了最先进的结果。',
            'creators_cn': json.dumps(['王华', '刘军']),
            'subjects_cn': json.dumps(['计算机科学', '自然语言处理']),
            'title_en': 'Transformer Models for Natural Language Processing',
            'abstract_en': 'We explore transformer architectures for NLP tasks and achieve state-of-the-art results.',
            'creators_en': json.dumps(['Wang Hua', 'Liu Jun']),
            'date': '2022-06-20T14:30:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Computer Science', 'Natural Language Processing']
        },
        {
            'id': 'chinaxiv-202212.00003',
            'title_cn': '图神经网络在社交网络分析中的应用',
            'abstract_cn': '本研究应用图神经网络分析社交网络结构。',
            'creators_cn': json.dumps(['陈晓']),
            'subjects_cn': json.dumps(['计算机科学', '图论']),
            'title_en': 'Graph Neural Networks for Social Network Analysis',
            'abstract_en': 'This study applies graph neural networks to analyze social network structures.',
            'creators_en': json.dumps(['Chen Xiao']),
            'date': '2022-12-31T23:59:59',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Computer Science', 'Graph Theory']
        },

        # Biology papers (2023)
        {
            'id': 'chinaxiv-202301.00004',
            'title_cn': 'CRISPR基因编辑在植物生物学中的应用',
            'abstract_cn': '我们展示了在水稻植物中成功进行基于CRISPR的基因编辑。',
            'creators_cn': json.dumps(['赵宇', '孙磊']),
            'subjects_cn': json.dumps(['生物学', '遗传学']),
            'title_en': 'CRISPR Gene Editing in Plant Biology',
            'abstract_en': 'We demonstrate successful CRISPR-based gene editing in rice plants.',
            'creators_en': json.dumps(['Zhao Yu', 'Sun Lei']),
            'date': '2023-01-10T09:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Biology', 'Genetics']
        },
        {
            'id': 'chinaxiv-202307.00005',
            'title_cn': '细胞应激中的蛋白质折叠机制',
            'abstract_cn': '本研究探讨了在各种应激条件下蛋白质的折叠机制。',
            'creators_cn': json.dumps(['吴丹']),
            'subjects_cn': json.dumps(['生物学', '生物化学']),
            'title_en': 'Protein Folding Mechanisms in Cellular Stress',
            'abstract_en': 'This research investigates protein folding under various stress conditions.',
            'creators_en': json.dumps(['Wu Dan']),
            'date': '2023-07-15T12:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Biology', 'Biochemistry']
        },

        # Physics papers (2024)
        {
            'id': 'chinaxiv-202401.00006',
            'title_cn': '超导量子比特量子计算',
            'abstract_cn': '我们报告了使用超导量子比特构建可扩展量子计算机的进展。',
            'creators_cn': json.dumps(['黄杰', '马强']),
            'subjects_cn': json.dumps(['物理学', '量子计算']),
            'title_en': 'Quantum Computing with Superconducting Qubits',
            'abstract_en': 'We report progress in building scalable quantum computers using superconducting qubits.',
            'creators_en': json.dumps(['Huang Jie', 'Ma Qiang']),
            'date': '2024-01-05T08:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Physics', 'Quantum Computing']
        },
        {
            'id': 'chinaxiv-202408.00007',
            'title_cn': '利用新型传感器探测暗物质',
            'abstract_cn': '本文描述了用于探测暗物质粒子的新型传感器技术。',
            'creators_cn': json.dumps(['徐林']),
            'subjects_cn': json.dumps(['物理学', '天体物理学']),
            'title_en': 'Dark Matter Detection Using Novel Sensors',
            'abstract_en': 'This paper describes new sensor technology for detecting dark matter particles.',
            'creators_en': json.dumps(['Xu Lin']),
            'date': '2024-08-20T16:00:00',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Physics', 'Astrophysics']
        },

        # Paper with Unicode (Chinese characters in English text - testing XSS)
        {
            'id': 'chinaxiv-202410.00008',
            'title_cn': '机器学习在农业中的应用',
            'abstract_cn': '本文探讨了机器学习在精准农业和智能农业中的应用。',
            'creators_cn': json.dumps(['郑梅']),
            'subjects_cn': json.dumps(['计算机科学', '农业']),
            'title_en': 'Machine Learning Applications in 农业 (Agriculture)',
            'abstract_en': 'This paper explores ML applications in precision agriculture and 智能farming.',
            'creators_en': json.dumps(['Zheng Mei']),
            'date': '2024-10-15T11:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Computer Science', 'Agriculture']
        },

        # Flagged paper (QA failed) - pending translation
        {
            'id': 'chinaxiv-202411.00009',
            'title_cn': '翻译失败示例',
            'abstract_cn': '这是一个翻译失败的示例论文。',
            'creators_cn': json.dumps(['测试作者']),
            'subjects_cn': json.dumps(['计算机科学']),
            'title_en': 'Incomplete Translation Example',
            'abstract_en': 'This translation has too many Chinese characters: 这是一个失败的翻译示例。',
            'creators_en': json.dumps(['Test Author']),
            'date': '2024-11-01T10:00:00',
            'has_figures': False,
            'has_full_text': False,
            'qa_status': 'fail',
            'text_status': 'failed',
            'subjects': ['Computer Science']
        },

        # Paper with special characters in title (testing XSS)
        {
            'id': 'chinaxiv-202412.00010',
            'title_cn': '安全测试论文',
            'abstract_cn': '本文测试Web应用中的XSS防护。',
            'creators_cn': json.dumps(['安全测试员']),
            'subjects_cn': json.dumps(['计算机科学']),
            'title_en': 'Testing <script>alert("XSS")</script> Security',
            'abstract_en': 'This paper tests XSS prevention in the web application.',
            'creators_en': json.dumps(['Security Tester']),
            'date': '2024-12-01T10:00:00',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Computer Science']
        },

        # Old paper (2020)
        {
            'id': 'chinaxiv-202001.00011',
            'title_cn': '早期COVID-19研究发现',
            'abstract_cn': '关于新型冠状病毒爆发的初步研究。',
            'creators_cn': json.dumps(['陈伟', '李红']),
            'subjects_cn': json.dumps(['生物学', '病毒学']),
            'title_en': 'Early COVID-19 Research Findings',
            'abstract_en': 'Initial research on the novel coronavirus outbreak.',
            'creators_en': json.dumps(['Chen Wei', 'Li Hong']),
            'date': '2020-02-15T09:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'subjects': ['Biology', 'Virology']
        },
    ]

    # Insert papers and subjects
    for paper in papers:
        cursor.execute("""
        INSERT INTO papers (
            id, title_cn, abstract_cn, creators_cn, subjects_cn,
            title_en, abstract_en, creators_en, date,
            has_figures, has_full_text, qa_status, text_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            paper['id'],
            paper['title_cn'],
            paper['abstract_cn'],
            paper['creators_cn'],
            paper['subjects_cn'],
            paper['title_en'],
            paper['abstract_en'],
            paper['creators_en'],
            paper['date'],
            paper['has_figures'],
            paper['has_full_text'],
            paper['qa_status'],
            paper['text_status'],
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
def sample_papers_with_figures(test_database):
    """
    Insert sample papers with figure_urls populated for testing figure display.

    Creates papers that have:
    - figure_urls JSON column populated with translated figure URLs
    - Different numbers of figures per paper
    - Mix of papers with and without figures
    - Both Chinese (_cn) and English (_en) columns
    """
    conn = psycopg2.connect(test_database)
    cursor = conn.cursor()

    papers = [
        # Paper with multiple translated figures
        {
            'id': 'chinaxiv-202201.00001',
            'title_cn': '深度学习神经网络优化方法',
            'abstract_cn': '本文提出了一种利用深度学习技术优化神经网络的新方法。',
            'creators_cn': json.dumps(['张伟', '李明']),
            'subjects_cn': json.dumps(['计算机科学', '人工智能']),
            'title_en': 'Deep Learning for Neural Network Optimization',
            'abstract_en': 'This paper presents a novel approach to optimize neural networks.',
            'creators_en': json.dumps(['Zhang Wei', 'Li Ming']),
            'date': '2022-01-15T10:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'figure_urls': json.dumps([
                {"number": 1, "url": "https://f004.backblazeb2.com/file/chinaxiv/figures/chinaxiv-202201.00001/translated/fig_1_en.png"},
                {"number": 2, "url": "https://f004.backblazeb2.com/file/chinaxiv/figures/chinaxiv-202201.00001/translated/fig_2_en.png"},
                {"number": 3, "url": "https://f004.backblazeb2.com/file/chinaxiv/figures/chinaxiv-202201.00001/translated/fig_3_en.png"}
            ]),
            'subjects': ['Computer Science', 'Artificial Intelligence']
        },
        # Paper with single translated figure
        {
            'id': 'chinaxiv-202206.00002',
            'title_cn': 'Transformer模型在自然语言处理中的应用',
            'abstract_cn': '我们探索了用于NLP任务的transformer架构。',
            'creators_cn': json.dumps(['王华']),
            'subjects_cn': json.dumps(['计算机科学']),
            'title_en': 'Transformer Models for NLP',
            'abstract_en': 'We explore transformer architectures for NLP tasks.',
            'creators_en': json.dumps(['Wang Hua']),
            'date': '2022-06-20T14:30:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'figure_urls': json.dumps([
                {"number": 1, "url": "https://f004.backblazeb2.com/file/chinaxiv/figures/chinaxiv-202206.00002/translated/fig_1_en.jpeg"}
            ]),
            'subjects': ['Computer Science']
        },
        # Paper without translated figures (has_figures=False)
        {
            'id': 'chinaxiv-202212.00003',
            'title_cn': '图神经网络',
            'abstract_cn': '本研究应用图神经网络。',
            'creators_cn': json.dumps(['陈晓']),
            'subjects_cn': json.dumps(['计算机科学']),
            'title_en': 'Graph Neural Networks',
            'abstract_en': 'This study applies graph neural networks.',
            'creators_en': json.dumps(['Chen Xiao']),
            'date': '2022-12-31T23:59:59',
            'has_figures': False,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'figure_urls': None,  # No figure URLs
            'subjects': ['Computer Science']
        },
        # Paper with has_figures=True but empty figure_urls (partial translation)
        {
            'id': 'chinaxiv-202301.00004',
            'title_cn': 'CRISPR基因编辑',
            'abstract_cn': '我们展示了基于CRISPR的基因编辑。',
            'creators_cn': json.dumps(['赵宇']),
            'subjects_cn': json.dumps(['生物学']),
            'title_en': 'CRISPR Gene Editing',
            'abstract_en': 'We demonstrate CRISPR-based gene editing.',
            'creators_en': json.dumps(['Zhao Yu']),
            'date': '2023-01-10T09:00:00',
            'has_figures': True,
            'has_full_text': True,
            'qa_status': 'pass',
            'text_status': 'complete',
            'figure_urls': '[]',  # Empty array - figures exist but not translated
            'subjects': ['Biology']
        },
    ]

    # Insert papers with figure_urls
    for paper in papers:
        cursor.execute("""
        INSERT INTO papers (
            id, title_cn, abstract_cn, creators_cn, subjects_cn,
            title_en, abstract_en, creators_en, date,
            has_figures, has_full_text, qa_status, text_status, figure_urls
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            paper['id'],
            paper['title_cn'],
            paper['abstract_cn'],
            paper['creators_cn'],
            paper['subjects_cn'],
            paper['title_en'],
            paper['abstract_en'],
            paper['creators_en'],
            paper['date'],
            paper['has_figures'],
            paper['has_full_text'],
            paper['qa_status'],
            paper['text_status'],
            paper['figure_urls']
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
        'ai_cs': {
            'label': 'AI / CS',
            'order': 1,
            'pinned': True,
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


@pytest.fixture
def sample_paper_pending_translation(test_database):
    """
    Insert a sample paper that is pending translation.

    Creates a paper with:
    - Chinese metadata in _cn columns
    - NULL _en columns (not yet translated)
    - text_status = 'pending'

    Used for testing db_utils.get_paper_for_translation()
    """
    conn = psycopg2.connect(test_database)
    cursor = conn.cursor()

    paper = {
        'id': 'chinaxiv-202501.00001',
        'title_cn': '待翻译论文标题',
        'abstract_cn': '这是一篇待翻译论文的摘要。',
        'creators_cn': json.dumps(['作者一', '作者二']),
        'subjects_cn': json.dumps(['计算机科学']),
        'date': '2025-01-01T10:00:00',
        'source_url': 'https://chinaxiv.org/abs/202501.00001',
        'pdf_url': 'https://chinaxiv.org/pdf/202501.00001',
        'processing_status': 'pending',
        'text_status': 'pending',
        'figures_status': 'pending',
        'pdf_status': 'pending',
    }

    cursor.execute("""
        INSERT INTO papers (
            id, title_cn, abstract_cn, creators_cn, subjects_cn,
            date, source_url, pdf_url,
            processing_status, text_status, figures_status, pdf_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        paper['id'],
        paper['title_cn'],
        paper['abstract_cn'],
        paper['creators_cn'],
        paper['subjects_cn'],
        paper['date'],
        paper['source_url'],
        paper['pdf_url'],
        paper['processing_status'],
        paper['text_status'],
        paper['figures_status'],
        paper['pdf_status'],
    ))

    # Insert subjects into paper_subjects table
    cursor.execute("""
        INSERT INTO paper_subjects (paper_id, subject)
        VALUES (%s, %s)
    """, (paper['id'], 'Computer Science'))

    conn.commit()
    conn.close()

    yield paper


@pytest.fixture(autouse=True)
def mock_openrouter_balance():
    """
    Mock OpenRouter balance check for all tests.

    The balance check was added to prevent pipeline spam when out of funds.
    Tests should not depend on real API calls, so we mock it to return
    sufficient balance by default.
    """
    with patch('src.pipeline.check_openrouter_balance') as mock:
        mock.return_value = (True, 10.0)  # Sufficient balance
        yield mock
