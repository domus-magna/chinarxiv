"""
Tests for translation request API endpoints.

Tests cover:
- Success cases (200)
- Duplicate detection (409)
- Invalid paper_id format (400)
- Missing paper_id (400)
- Bad JSON (non-dict types like arrays, strings, bools)
"""

import json
import time


class TestFigureTranslationRequest:
    """Tests for POST /api/request-figure-translation endpoint."""

    def test_success(self, client):
        """Valid request should return 200 and success message."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00001'}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert 'logged' in data['message'].lower()

    def test_duplicate_request(self, client):
        """Second request within 60s window should return 409."""
        # First request succeeds
        response1 = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00002'}),
            content_type='application/json'
        )
        assert response1.status_code == 200

        # Second request (same IP + paper) should be duplicate
        response2 = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00002'}),
            content_type='application/json'
        )
        assert response2.status_code == 409
        data = response2.get_json()
        assert data['success'] is False
        assert 'duplicate' in data['message'].lower()

    def test_invalid_paper_id_format(self, client):
        """Invalid paper_id format should return 400."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 'invalid-format'}),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'format' in data['message'].lower()

    def test_missing_paper_id(self, client):
        """Missing paper_id should return 400."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False

    def test_paper_id_wrong_type(self, client):
        """Non-string paper_id should return 400."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 12345}),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False

    def test_json_array_body(self, client):
        """JSON array instead of object should return 400 (not 500)."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps(['chinaxiv-202510.00001']),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'json' in data['message'].lower()

    def test_json_string_body(self, client):
        """JSON string instead of object should return 400 (not 500)."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps('chinaxiv-202510.00001'),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'json' in data['message'].lower()

    def test_json_bool_body(self, client):
        """JSON boolean instead of object should return 400 (not 500)."""
        response = client.post(
            '/api/request-figure-translation',
            data=json.dumps(True),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'json' in data['message'].lower()

    def test_malformed_json(self, client):
        """Malformed JSON should return 400."""
        response = client.post(
            '/api/request-figure-translation',
            data='{invalid json}',
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False


class TestTextTranslationRequest:
    """Tests for POST /api/request-text-translation endpoint."""

    def test_success(self, client):
        """Valid request should return 200 and success message."""
        response = client.post(
            '/api/request-text-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00003'}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert 'logged' in data['message'].lower()

    def test_duplicate_request(self, client):
        """Second request within 60s window should return 409."""
        # First request succeeds
        response1 = client.post(
            '/api/request-text-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00004'}),
            content_type='application/json'
        )
        assert response1.status_code == 200

        # Second request (same IP + paper) should be duplicate
        response2 = client.post(
            '/api/request-text-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00004'}),
            content_type='application/json'
        )
        assert response2.status_code == 409
        data = response2.get_json()
        assert data['success'] is False
        assert 'duplicate' in data['message'].lower()

    def test_invalid_paper_id_format(self, client):
        """Invalid paper_id format should return 400."""
        response = client.post(
            '/api/request-text-translation',
            data=json.dumps({'paper_id': 'bad-id'}),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False
        assert 'format' in data['message'].lower()

    def test_json_array_body(self, client):
        """JSON array instead of object should return 400 (not 500)."""
        response = client.post(
            '/api/request-text-translation',
            data=json.dumps(['chinaxiv-202510.00001']),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data['success'] is False


class TestTranslationRequestIsolation:
    """Tests for isolation between figure and text requests."""

    def test_figure_and_text_independent(self, client):
        """Figure and text requests for same paper should be independent."""
        paper_id = 'chinaxiv-202510.00005'

        # Figure request succeeds
        response1 = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': paper_id}),
            content_type='application/json'
        )
        assert response1.status_code == 200

        # Text request for same paper also succeeds (different type)
        response2 = client.post(
            '/api/request-text-translation',
            data=json.dumps({'paper_id': paper_id}),
            content_type='application/json'
        )
        assert response2.status_code == 200

    def test_different_papers_independent(self, client):
        """Requests for different papers should be independent."""
        # Request for paper 1
        response1 = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00006'}),
            content_type='application/json'
        )
        assert response1.status_code == 200

        # Request for paper 2 (should also succeed)
        response2 = client.post(
            '/api/request-figure-translation',
            data=json.dumps({'paper_id': 'chinaxiv-202510.00007'}),
            content_type='application/json'
        )
        assert response2.status_code == 200
