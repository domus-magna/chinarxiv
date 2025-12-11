"""
Tests for src/db_utils.py - Database utilities for translation pipeline.

These tests verify:
- get_paper_for_translation() loads Chinese metadata correctly
- save_translation_result() saves English translations correctly
- update_chinese_metadata() updates _cn columns correctly
- get_papers_needing_translation() returns correct paper IDs
"""

import json
import os

import psycopg2

from src.db_utils import (
    get_paper_for_translation,
    save_translation_result,
    update_chinese_metadata,
    get_papers_needing_translation,
)


class TestGetPaperForTranslation:
    """Tests for get_paper_for_translation()."""

    def test_loads_paper_with_cn_columns(self, sample_paper_pending_translation, test_database):
        """Paper with _cn columns populated should be loaded correctly."""
        os.environ['DATABASE_URL'] = test_database

        paper_id = sample_paper_pending_translation['id']
        result = get_paper_for_translation(paper_id)

        assert result is not None
        assert result['id'] == paper_id
        assert result['title'] == '待翻译论文标题'
        assert result['abstract'] == '这是一篇待翻译论文的摘要。'
        assert result['creators'] == ['作者一', '作者二']
        assert result['pdf_url'] == 'https://chinaxiv.org/pdf/202501.00001'
        assert result['source_url'] == 'https://chinaxiv.org/abs/202501.00001'

    def test_returns_none_for_nonexistent_paper(self, test_database):
        """Non-existent paper should return None."""
        os.environ['DATABASE_URL'] = test_database

        result = get_paper_for_translation('chinaxiv-999999.99999')

        assert result is None

    def test_returns_none_for_paper_without_chinese(self, test_database):
        """Paper with no Chinese metadata should return None."""
        os.environ['DATABASE_URL'] = test_database

        # Insert a paper with no Chinese metadata
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO papers (id, text_status)
            VALUES ('chinaxiv-202502.00001', 'complete')
        """)
        conn.commit()
        conn.close()

        result = get_paper_for_translation('chinaxiv-202502.00001')

        assert result is None

    def test_uses_provided_connection(self, sample_paper_pending_translation, test_database):
        """Should use provided connection instead of creating new one."""
        from psycopg2.extras import RealDictCursor
        os.environ['DATABASE_URL'] = test_database
        conn = psycopg2.connect(test_database, cursor_factory=RealDictCursor)

        try:
            paper_id = sample_paper_pending_translation['id']
            result = get_paper_for_translation(paper_id, conn=conn)

            assert result is not None
            assert result['id'] == paper_id
        finally:
            conn.close()


class TestSaveTranslationResult:
    """Tests for save_translation_result()."""

    def test_saves_translation_to_database(self, sample_paper_pending_translation, test_database):
        """Translation should be saved to database correctly."""
        os.environ['DATABASE_URL'] = test_database
        paper_id = sample_paper_pending_translation['id']

        translation = {
            'title_en': 'Translated Title',
            'abstract_en': 'Translated abstract content.',
            'creators_en': ['Author One', 'Author Two'],
            'body_md': '# Introduction\n\nThis is the translated body content. It needs to be over 100 characters long for has_full_text to be True. So here is some more text to make it longer.',
            '_qa_status': 'pass',
        }

        result = save_translation_result(paper_id, translation)

        assert result is True

        # Verify in database
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT title_en, abstract_en, creators_en, body_md,
                   has_full_text, qa_status, text_status
            FROM papers WHERE id = %s
        """, (paper_id,))
        row = cursor.fetchone()
        conn.close()

        assert row[0] == 'Translated Title'
        assert row[1] == 'Translated abstract content.'
        # creators_en is JSONB so it comes back as list directly (not JSON string)
        creators = row[2] if isinstance(row[2], list) else json.loads(row[2])
        assert creators == ['Author One', 'Author Two']
        assert '# Introduction' in row[3]
        assert row[4] is True  # has_full_text
        assert row[5] == 'pass'  # qa_status
        assert row[6] == 'complete'  # text_status

    def test_returns_false_for_nonexistent_paper(self, test_database):
        """Saving to non-existent paper should return False."""
        os.environ['DATABASE_URL'] = test_database

        translation = {
            'title_en': 'Test',
            'abstract_en': 'Test',
        }

        result = save_translation_result('chinaxiv-999999.99999', translation)

        assert result is False

    def test_preserves_cn_columns(self, sample_paper_pending_translation, test_database):
        """Saving translation should not modify _cn columns."""
        os.environ['DATABASE_URL'] = test_database
        paper_id = sample_paper_pending_translation['id']

        translation = {
            'title_en': 'New English Title',
            'abstract_en': 'New English abstract.',
        }

        save_translation_result(paper_id, translation)

        # Verify _cn columns unchanged
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT title_cn, abstract_cn FROM papers WHERE id = %s
        """, (paper_id,))
        row = cursor.fetchone()
        conn.close()

        assert row[0] == '待翻译论文标题'  # Original Chinese title
        assert row[1] == '这是一篇待翻译论文的摘要。'  # Original Chinese abstract


class TestUpdateChineseMetadata:
    """Tests for update_chinese_metadata()."""

    def test_updates_cn_columns(self, test_database):
        """Should update Chinese metadata columns."""
        os.environ['DATABASE_URL'] = test_database

        # Insert a paper without Chinese metadata
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO papers (id, title_en, text_status)
            VALUES ('chinaxiv-202503.00001', 'Existing English Title', 'complete')
        """)
        conn.commit()
        conn.close()

        result = update_chinese_metadata(
            'chinaxiv-202503.00001',
            title_cn='新中文标题',
            abstract_cn='新中文摘要',
            creators_cn=['作者甲', '作者乙'],
            subjects_cn=['主题一', '主题二'],
        )

        assert result is True

        # Verify in database
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT title_cn, abstract_cn, creators_cn, subjects_cn
            FROM papers WHERE id = %s
        """, ('chinaxiv-202503.00001',))
        row = cursor.fetchone()
        conn.close()

        assert row[0] == '新中文标题'
        assert row[1] == '新中文摘要'
        # JSONB columns come back as list directly
        creators = row[2] if isinstance(row[2], list) else json.loads(row[2])
        subjects = row[3] if isinstance(row[3], list) else json.loads(row[3])
        assert creators == ['作者甲', '作者乙']
        assert subjects == ['主题一', '主题二']

    def test_returns_false_for_nonexistent_paper(self, test_database):
        """Updating non-existent paper should return False."""
        os.environ['DATABASE_URL'] = test_database

        result = update_chinese_metadata(
            'chinaxiv-999999.99999',
            title_cn='Test',
            abstract_cn='Test',
            creators_cn=[],
            subjects_cn=[],
        )

        assert result is False


class TestGetPapersNeedingTranslation:
    """Tests for get_papers_needing_translation()."""

    def test_returns_pending_papers(self, sample_paper_pending_translation, test_database):
        """Should return papers with text_status='pending'."""
        os.environ['DATABASE_URL'] = test_database

        papers = get_papers_needing_translation()

        assert sample_paper_pending_translation['id'] in papers

    def test_returns_failed_papers(self, test_database):
        """Should return papers with text_status='failed'."""
        os.environ['DATABASE_URL'] = test_database

        # Insert a failed paper with Chinese metadata
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO papers (id, title_cn, text_status)
            VALUES ('chinaxiv-202504.00001', '失败的论文', 'failed')
        """)
        conn.commit()
        conn.close()

        papers = get_papers_needing_translation()

        assert 'chinaxiv-202504.00001' in papers

    def test_excludes_complete_papers(self, sample_papers, test_database):
        """Should not return papers with text_status='complete'."""
        os.environ['DATABASE_URL'] = test_database

        papers = get_papers_needing_translation()

        # sample_papers fixture has text_status='complete' for most papers
        # Verify none of the complete papers are returned
        complete_paper_id = 'chinaxiv-202201.00001'  # Known complete paper
        assert complete_paper_id not in papers

    def test_respects_limit(self, test_database):
        """Should respect the limit parameter."""
        os.environ['DATABASE_URL'] = test_database

        # Insert multiple pending papers
        conn = psycopg2.connect(test_database)
        cursor = conn.cursor()
        for i in range(10):
            cursor.execute("""
                INSERT INTO papers (id, title_cn, text_status)
                VALUES (%s, %s, 'pending')
            """, (f'chinaxiv-202505.{i:05d}', f'论文{i}'))
        conn.commit()
        conn.close()

        papers = get_papers_needing_translation(limit=5)

        assert len(papers) <= 5
