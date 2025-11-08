"""
Tests for consolidated monitoring service functionality.
"""

import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch
from pathlib import Path

from src.monitoring import MonitoringService


class TestMonitoringService:
    """Test cases for consolidated MonitoringService."""
    
    def setup_method(self):
        """Setup test environment."""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create service with proper initialization
        self.service = MonitoringService()
        self.service.data_dir = Path(self.temp_dir)
        self.service.alerts = []
        self.service.analytics = {}
        self.service.performance = {}
        self.service.max_alerts = 1000
        self.service.max_analytics_entries = 10000
        self.service.max_performance_entries = 5000
        self.service.retention_days = 30
    
    def teardown_method(self):
        """Cleanup test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_create_alert(self):
        """Test alert creation with all levels."""
        # Test info alert
        alert = self.service.create_alert("info", "Test Info", "Test message")
        assert alert["level"] == "info"
        assert alert["title"] == "Test Info"
        assert alert["message"] == "Test message"
        assert isinstance(alert["timestamp"], str)
        
        # Test warning alert
        alert = self.service.create_alert("warning", "Test Warning", "Warning message")
        assert alert["level"] == "warning"
        
        # Test error alert
        alert = self.service.create_alert("error", "Test Error", "Error message")
        assert alert["level"] == "error"
        
        # Test critical alert
        alert = self.service.create_alert("critical", "Test Critical", "Critical message")
        assert alert["level"] == "critical"
    
    def test_get_alerts(self):
        """Test getting alerts."""
        # Create some alerts
        self.service.create_alert("info", "Alert 1", "Message 1")
        self.service.create_alert("warning", "Alert 2", "Message 2")
        self.service.create_alert("error", "Alert 3", "Message 3")
        
        # Get all alerts
        alerts = self.service.get_alerts()
        assert len(alerts) == 3
        
        # Get alerts with limit
        limited_alerts = self.service.get_alerts(limit=2)
        assert len(limited_alerts) == 2
        
        # Get alerts with limit larger than available
        many_alerts = self.service.get_alerts(limit=10)
        assert len(many_alerts) == 3
    
    def test_cleanup_alerts(self):
        """Test cleaning up old alerts."""
        # Create alerts
        self.service.create_alert("info", "Recent Alert", "Recent message")
        
        # Manually add old alert
        old_alert = {
            "level": "info",
            "title": "Old Alert",
            "message": "Old message",
            "timestamp": (datetime.now() - timedelta(days=10)).isoformat(),
            "source": "test",
            "metadata": {}
        }
        self.service.alerts.append(old_alert)
        
        # Cleanup old alerts (older than 7 days)
        self.service.cleanup_alerts(7)
        
        # Check that old alert is removed
        assert len(self.service.alerts) == 1
        assert self.service.alerts[0]["title"] == "Recent Alert"
    
    def test_track_page_view(self):
        """Test page view tracking."""
        self.service.track_page_view("/test-page", user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        
        # Check that data was tracked
        assert "page_views" in self.service.analytics
        assert len(self.service.analytics["page_views"]) == 1
        
        page_view = self.service.analytics["page_views"][0]
        assert page_view["page"] == "/test-page"
        assert page_view["user_agent"] == "Mozilla/5.0"
        assert page_view["ip_address"] == "192.168.1.1"
        assert isinstance(datetime.fromisoformat(page_view["timestamp"]), datetime)
    
    def test_track_search(self):
        """Test search query tracking."""
        self.service.track_search("machine learning", 25, user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        
        # Check that data was tracked
        assert "search_queries" in self.service.analytics
        assert len(self.service.analytics["search_queries"]) == 1
        
        search_query = self.service.analytics["search_queries"][0]
        assert search_query["query"] == "machine learning"
        assert search_query["results"] == 25
        assert search_query["user_agent"] == "Mozilla/5.0"
        assert search_query["ip_address"] == "192.168.1.1"
    
    def test_get_analytics(self):
        """Test getting analytics summary."""
        # Add some analytics data
        self.service.track_page_view("/page1", user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        self.service.track_page_view("/page2", user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        self.service.track_search("query1", 10, user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        
        # Get analytics summary
        analytics = self.service.get_analytics(days=7)
        
        assert "page_views" in analytics
        assert "search_queries" in analytics
        assert len(analytics["page_views"]) == 2
        assert len(analytics["search_queries"]) == 1
    
    def test_record_metric(self):
        """Test performance metric recording."""
        self.service.record_metric("test_metric", 100.5, unit="ms", metadata={"key": "value"})
        
        # Check that data was recorded
        assert "metrics" in self.service.performance
        assert len(self.service.performance["metrics"]) == 1
        
        metric = self.service.performance["metrics"][0]
        assert metric["name"] == "test_metric"
        assert metric["value"] == 100.5
        assert metric["unit"] == "ms"
        assert metric["metadata"] == {"key": "value"}
        assert isinstance(datetime.fromisoformat(metric["timestamp"]), datetime)
    
    def test_get_performance(self):
        """Test getting performance summary."""
        # Add some performance metrics
        self.service.record_metric("response_time", 100, unit="ms")
        self.service.record_metric("response_time", 200, unit="ms")
        self.service.record_metric("cpu_usage", 50, unit="percent")
        
        # Get performance summary
        performance = self.service.get_performance(days=7)
        
        assert "metrics" in performance
        assert len(performance["metrics"]) == 3
        
        # Check that metrics are grouped by name
        response_times = [m for m in performance["metrics"] if m["name"] == "response_time"]
        assert len(response_times) == 2
        
        cpu_usages = [m for m in performance["metrics"] if m["name"] == "cpu_usage"]
        assert len(cpu_usages) == 1
    
    def test_optimize_site(self):
        """Test site optimization."""
        # Mock optimization results
        with patch.object(self.service, '_optimize_search_index') as mock_search:
            with patch.object(self.service, '_optimize_images') as mock_images:
                mock_search.return_value = (True, "Search index optimized")
                mock_images.return_value = (True, "Images optimized")
                
                results = self.service.optimize_site()
                
                assert "search_index" in results
                assert "images" in results
                assert results["search_index"]["success"] is True
                assert results["images"]["success"] is True
    
    def test_get_status(self):
        """Test getting complete system status."""
        # Add some data
        self.service.create_alert("info", "Test Alert", "Test message")
        self.service.track_page_view("/test", user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        self.service.record_metric("test_metric", 100, unit="ms")
        
        # Get status
        status = self.service.get_status()
        
        assert "alerts" in status
        assert "analytics" in status
        assert "performance" in status
        assert "timestamp" in status
        
        # Check that data is included
        assert len(status["alerts"]) == 1
        assert len(status["analytics"]["page_views"]) == 1
        assert len(status["performance"]["metrics"]) == 1
    
    def test_cleanup_old_data(self):
        """Test cleaning up old monitoring data."""
        # Add recent data
        self.service.create_alert("info", "Recent Alert", "Recent message")
        self.service.track_page_view("/recent", user_agent="Mozilla/5.0", ip_address="192.168.1.1")
        self.service.record_metric("recent_metric", 100, unit="ms")
        
        # Manually add old data
        old_alert = {
            "level": "info",
            "title": "Old Alert",
            "message": "Old message",
            "timestamp": (datetime.now() - timedelta(days=100)).isoformat(),
            "source": "test",
            "metadata": {}
        }
        self.service.alerts.append(old_alert)
        
        old_page_view = {
            "page": "/old",
            "user_agent": "Mozilla/5.0",
            "ip_address": "192.168.1.1",
            "timestamp": (datetime.now() - timedelta(days=100)).isoformat()
        }
        if "page_views" not in self.service.analytics:
            self.service.analytics["page_views"] = []
        self.service.analytics["page_views"].append(old_page_view)
        
        old_metric = {
            "name": "old_metric",
            "value": 50,
            "unit": "ms",
            "timestamp": (datetime.now() - timedelta(days=100)).isoformat(),
            "metadata": {}
        }
        if "metrics" not in self.service.performance:
            self.service.performance["metrics"] = []
        self.service.performance["metrics"].append(old_metric)
        
        # Cleanup old data
        self.service.cleanup_old_data(30)
        
        # Check that old data is removed
        assert len(self.service.alerts) == 1
        assert self.service.alerts[0]["title"] == "Recent Alert"
        
        assert len(self.service.analytics["page_views"]) == 1
        assert self.service.analytics["page_views"][0]["page"] == "/recent"
        
        assert len(self.service.performance["metrics"]) == 1
        assert self.service.performance["metrics"][0]["name"] == "recent_metric"
    
    def test_consolidated_functionality(self):
        """Test all consolidated monitoring functionality."""
        # Test alerts
        alert = self.service.create_alert("info", "Test", "Test message")
        assert alert["level"] == "info"
        
        # Test analytics
        self.service.track_page_view("/test")
        analytics = self.service.get_analytics()
        assert len(analytics["page_views"]) > 0
        
        # Test performance
        self.service.record_metric("test_metric", 100.0)
        performance = self.service.get_performance()
        assert len(performance["metrics"]) > 0
        
        # Test combined status
        status = self.service.get_status()
        assert "alerts" in status
        assert "analytics" in status
        assert "performance" in status
