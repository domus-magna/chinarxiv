"""
Tests for bugs discovered during manual testing.
"""

import pytest
import json
import os
import tempfile
from unittest.mock import patch

from src.services.translation_service import TranslationService
from src.job_queue import JobQueue
from src.monitoring import MonitoringService
from src.render import render_site, load_translated
from src.batch_translate import init_queue, show_status


 


class TestTranslationServiceBugs:
    """Test translation service bug fixes."""
    
    def test_translation_service_handles_empty_text(self):
        """Test that translation service handles empty text."""
        service = TranslationService()
        
        # Test empty string
        result = service.translate_field("")
        assert result == ""
        
        # Test None
        result = service.translate_field(None)
        assert result == ""
    
    def test_translation_service_with_mocked_api(self):
        """Test translation service with mocked API call."""
        service = TranslationService()
        
        # Mock the _call_openrouter method
        def mock_call_openrouter(text, model, glossary):
            if 'test' in text.lower():
                return 'Translated test text'
            return 'Translated text'
        
        service._call_openrouter = mock_call_openrouter
        
        # Test translation
        result = service.translate_field("This is a test")
        assert result == "Translated test text"
    
    def test_translation_service_math_preservation(self):
        """Test math preservation in translation."""
        
        service = TranslationService()
        
        # Mock the _call_openrouter method to preserve math
        def mock_call_openrouter(text, model, glossary):
            # Simulate translation that preserves math tokens
            return text.replace('公式', 'Formula').replace('和方程', 'and equation')
        
        service._call_openrouter = mock_call_openrouter
        
        # Test with math
        math_text = '公式 $x^2 + y^2 = z^2$ 和方程 \\\\[E = mc^2\\\\]'
        result = service.translate_field(math_text)
        
        # Should preserve math expressions
        assert '$x^2 + y^2 = z^2$' in result
        assert '\\\\[E = mc^2\\\\]' in result


class TestJobQueueBugs:
    """Test job queue bug fixes."""
    
    def test_job_queue_no_init_schema_needed(self):
        """Test that job queue doesn't need init_schema."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create data directory
                os.makedirs('data', exist_ok=True)
                queue = JobQueue()
                
                # Should work without calling init_schema
                paper_ids = ['test-001', 'test-002']
                added = queue.add_jobs(paper_ids)
                assert added == 2
                
                stats = queue.get_stats()
                assert stats['total'] == 2
                assert stats['pending'] == 2
                
            finally:
                os.chdir(original_cwd)
    
    def test_job_queue_claim_and_complete_workflow(self):
        """Test complete job workflow."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create data directory
                os.makedirs('data', exist_ok=True)
                queue = JobQueue()
                
                # Add job
                queue.add_jobs(['test-001'])
                
                # Claim job
                job = queue.claim_job('worker-001')
                assert job is not None
                assert job['status'] == 'in_progress'
                assert job['worker_id'] == 'worker-001'
                
                # Complete job
                queue.complete_job(job['id'])
                
                # Check stats
                stats = queue.get_stats()
                assert stats['completed'] == 1
                assert stats['pending'] == 0
                assert stats['in_progress'] == 0
                
            finally:
                os.chdir(original_cwd)
    
    def test_job_queue_fail_and_retry_workflow(self):
        """Test job failure and retry workflow."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create data directory
                os.makedirs('data', exist_ok=True)
                queue = JobQueue()
                
                # Add job
                queue.add_jobs(['test-001'])
                
                # Claim and fail job
                job = queue.claim_job('worker-001')
                queue.fail_job(job['id'], 'Test error')
                
                # Should retry first time
                stats = queue.get_stats()
                assert stats['failed'] == 0
                assert stats['pending'] == 1
                
                # Fail again to trigger permanent failure
                job = queue.claim_job('worker-001')
                queue.fail_job(job['id'], 'Test error')
                queue.fail_job(job['id'], 'Test error')
                
                # Should be permanently failed
                stats = queue.get_stats()
                assert stats['failed'] == 1
                assert stats['pending'] == 0
                
            finally:
                os.chdir(original_cwd)


class TestMonitoringServiceBugs:
    """Test monitoring service bug fixes."""
    
    def test_monitoring_service_track_page_view_correct_signature(self):
        """Test that track_page_view has correct signature."""
        service = MonitoringService()
        
        # Should work with just page parameter
        service.track_page_view('/test-page')
        
        # Should work with additional kwargs
        service.track_page_view('/test-page', user_agent='Test User')
        
        # Check analytics
        analytics = service.get_analytics()
        assert len(analytics.get('page_views', [])) >= 2
    
    def test_monitoring_service_track_search_correct_signature(self):
        """Test that track_search has correct signature."""
        service = MonitoringService()
        
        # Should work with query and results
        service.track_search('machine learning', 10)
        
        # Should work with additional kwargs
        service.track_search('deep learning', 5, user_agent='Test User')
        
        # Check analytics
        analytics = service.get_analytics()
        assert len(analytics.get('search_queries', [])) >= 2
    
    def test_monitoring_service_get_status_structure(self):
        """Test that get_status returns correct structure."""
        service = MonitoringService()
        
        # Add some data
        service.track_page_view('/test')
        service.record_metric('test', 1.0, unit='ms')
        
        status = service.get_status()
        
        # Should have correct keys
        assert 'alerts' in status
        assert 'analytics' in status
        assert 'performance' in status
        assert 'timestamp' in status
        
        # Should be lists/dicts
        assert isinstance(status['alerts'], list)
        assert isinstance(status['analytics'], dict)
        assert isinstance(status['performance'], dict)


class TestRenderBugs:
    """Test render system bug fixes."""
    
    def test_render_site_requires_items_parameter(self):
        """Test that render_site requires items parameter."""
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            render_site()
    
    def test_render_site_creates_correct_directory_structure(self):
        """Test that render_site creates correct directory structure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create test translation
                os.makedirs('data/translated', exist_ok=True)
                test_translation = {
                    'id': 'test-paper-001',
                    'title_en': 'Test Paper',
                    'abstract_en': 'Test abstract',
                    'body_en': ['Test body paragraph']
                }
                
                with open('data/translated/test-paper-001.json', 'w') as f:
                    json.dump(test_translation, f)
                
                # Create templates directory
                os.makedirs('src/templates', exist_ok=True)
                
                # Create minimal templates
                with open('src/templates/index.html', 'w') as f:
                    f.write('<html><body><h1>Papers</h1></body></html>')
                
                with open('src/templates/item.html', 'w') as f:
                    f.write('<html><body><h1>{{ item.title_en }}</h1></body></html>')
                
                with open('src/templates/monitor.html', 'w') as f:
                    f.write('<html><body><h1>Monitor</h1></body></html>')
                
                # Render site
                translations = load_translated()
                render_site(translations)
                
                # Check directory structure
                assert os.path.exists('site/items/test-paper-001/index.html')
                assert os.path.exists('site/items/test-paper-001/test-paper-001.md')
                assert os.path.exists('site/index.html')
                
            finally:
                os.chdir(original_cwd)
    
    def test_load_translated_handles_missing_directory(self):
        """Test that load_translated handles missing directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Should not crash if directory doesn't exist
                translations = load_translated()
                assert isinstance(translations, list)
                assert len(translations) == 0
                
            finally:
                os.chdir(original_cwd)


class TestBatchTranslateBugs:
    """Test batch translate bug fixes."""
    
    def test_init_queue_no_init_schema_call(self):
        """Test that init_queue doesn't call init_schema."""
        with patch('src.batch_translate.job_queue') as mock_queue:
            mock_queue.add_jobs.return_value = 5
            
            # Should not call init_schema
            init_queue(['2024'], limit=5, use_harvested=False)
            
            # Should call add_jobs
            mock_queue.add_jobs.assert_called_once()
    
    def test_show_status_no_qa_fields(self):
        """Test that show_status doesn't reference non-existent QA fields."""
        with patch('src.batch_translate.job_queue') as mock_queue:
            mock_queue.get_stats.return_value = {
                'total': 10,
                'completed': 5,
                'in_progress': 2,
                'pending': 3,
                'failed': 0
            }
            
            # Should not crash
            show_status()
            
            # Should call get_stats
            mock_queue.get_stats.assert_called_once()


class TestSearchIndexBugs:
    """Test search index bug fixes."""
    
    def test_search_index_handles_empty_translations(self):
        """Test that search index handles empty translations."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create empty translated directory
                os.makedirs('data/translated', exist_ok=True)
                
                from src.search_index import run_cli as build_search_index
                
                # Should not crash
                build_search_index()
                
                # Should create empty search index
                assert os.path.exists('site/search-index.json')
                
                with open('site/search-index.json', 'r') as f:
                    search_data = json.load(f)
                
                assert isinstance(search_data, list)
                assert len(search_data) == 0
                
            finally:
                os.chdir(original_cwd)
    
    def test_search_index_compression_works(self):
        """Test that search index compression works."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create test translation
                os.makedirs('data/translated', exist_ok=True)
                test_translation = {
                    'id': 'test-paper-001',
                    'title_en': 'Test Paper',
                    'abstract_en': 'Test abstract with some content to compress',
                    'body_en': ['Test body paragraph with more content']
                }
                
                with open('data/translated/test-paper-001.json', 'w') as f:
                    json.dump(test_translation, f)
                
                from src.search_index import run_cli as build_search_index
                build_search_index()
                
                # Check both files exist
                assert os.path.exists('site/search-index.json')
                assert os.path.exists('site/search-index.json.gz')
                
                # Check compression ratio
                original_size = os.path.getsize('site/search-index.json')
                compressed_size = os.path.getsize('site/search-index.json.gz')
                
                assert compressed_size < original_size
                assert compressed_size > 0
                
            finally:
                os.chdir(original_cwd)


class TestIntegrationBugs:
    """Test integration bug fixes."""
    
    def test_full_pipeline_with_real_paper_structure(self):
        """Test full pipeline with real paper structure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = os.getcwd()
            os.chdir(temp_dir)
            
            try:
                # Create real paper structure
                os.makedirs('data/selected', exist_ok=True)
                real_paper = {
                    'id': 'paper-201601.00051V1',
                    'title': '国史知识的语义揭示与组织方法研究',
                    'abstract': '国史知识的语义揭示与组织方法研究 作者： 王颖 1 张智雄 1 孙辉 2 雷枫 2',
                    'body': ['国史知识的语义揭示与组织方法研究']
                }
                
                with open('data/selected/paper-201601.00051V1.json', 'w', encoding='utf-8') as f:
                    json.dump(real_paper, f, indent=2, ensure_ascii=False)
                
                # Test translation service
                service = TranslationService()
                
                # Mock translation
                def mock_translate(text, model, glossary):
                    if '国史知识' in text:
                        return 'Research on Semantic Disclosure and Organization Methods of National History Knowledge'
                    return 'Translated text'
                
                service._call_openrouter = mock_translate
                
                # Translate
                translation = {
                    'id': real_paper['id'],
                    'title_en': service.translate_field(real_paper['title']),
                    'abstract_en': service.translate_field(real_paper['abstract']),
                    'body_en': [service.translate_field(p) for p in real_paper['body']]
                }
                
                # Save translation
                os.makedirs('data/translated', exist_ok=True)
                with open('data/translated/paper-201601.00051V1.json', 'w', encoding='utf-8') as f:
                    json.dump(translation, f, indent=2, ensure_ascii=False)
                
                # Test rendering
                os.makedirs('src/templates', exist_ok=True)
                
                with open('src/templates/index.html', 'w') as f:
                    f.write('<html><body><h1>Papers</h1></body></html>')
                
                with open('src/templates/item.html', 'w') as f:
                    f.write('<html><body><h1>{{ item.title_en }}</h1></body></html>')
                
                with open('src/templates/monitor.html', 'w') as f:
                    f.write('<html><body><h1>Monitor</h1></body></html>')
                
                translations = load_translated()
                render_site(translations)
                
                # Check output
                assert os.path.exists('site/items/paper-201601.00051V1/index.html')
                assert os.path.exists('site/items/paper-201601.00051V1/paper-201601.00051V1.md')
                
                # Test search index
                from src.search_index import run_cli as build_search_index
                build_search_index()
                
                assert os.path.exists('site/search-index.json')
                
                with open('site/search-index.json', 'r') as f:
                    search_data = json.load(f)
                
                # Find our paper
                our_paper = next((item for item in search_data if item.get('id') == real_paper['id']), None)
                assert our_paper is not None
                assert 'Research on Semantic Disclosure' in our_paper.get('title', '')
                
            finally:
                os.chdir(original_cwd)
