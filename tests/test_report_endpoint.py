"""
Tests for the /api/report endpoint.

These cover basic validation and DB insertion behavior for user reports.
"""

import json

import psycopg2


def _fetch_reports(test_database):
    conn = psycopg2.connect(test_database)
    cur = conn.cursor()
    cur.execute("SELECT paper_id, issue_type, description, context FROM user_reports ORDER BY created_at ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def test_report_endpoint_valid_request(client, test_database):
    response = client.post(
        "/api/report",
        json={
            "type": "translation",
            "description": "This translation has errors in the abstract section.",
            "context": {"paperId": "chinaxiv-202201.00001", "url": "/items/chinaxiv-202201.00001"},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    rows = _fetch_reports(test_database)
    assert len(rows) == 1
    paper_id, issue_type, description, context_json = rows[0]
    assert paper_id == "chinaxiv-202201.00001"
    assert issue_type == "translation"
    assert "abstract section" in description
    context_obj = json.loads(context_json) if isinstance(context_json, str) else context_json
    assert context_obj["paperId"] == "chinaxiv-202201.00001"


def test_report_endpoint_rejects_invalid_json(client):
    response = client.post(
        "/api/report",
        data="not json",
        content_type="application/json",
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Invalid JSON" in payload["message"]


def test_report_endpoint_rejects_unknown_type(client):
    response = client.post(
        "/api/report",
        json={
            "type": "made-up",
            "description": "This is a valid-length description but invalid type.",
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Invalid type" in payload["message"]


def test_report_endpoint_rejects_short_description(client):
    response = client.post(
        "/api/report",
        json={
            "type": "other",
            "description": "short",
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Invalid description" in payload["message"]


def test_report_endpoint_rejects_too_long_description(client):
    response = client.post(
        "/api/report",
        json={
            "type": "other",
            "description": "a" * 6000,
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Description too long" in payload["message"]


def test_report_endpoint_rejects_too_large_context(client):
    # 11k bytes context to exceed MAX_REPORT_CONTEXT_SIZE (10k)
    response = client.post(
        "/api/report",
        json={
            "type": "other",
            "description": "This is a valid description with too large context.",
            "context": {"blob": "x" * 11000},
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Context too large" in payload["message"]
