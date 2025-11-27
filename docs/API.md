# API Documentation

## Overview
This document describes the API endpoints and functionality available in the ChinaXiv Translations system.

## Base URL
- **Production**: `https://chinarxiv.org`
- **Local Development**: `http://localhost:8001`

## Authentication
Most endpoints are public. The monitoring dashboard requires authentication:
- **Username**: Set via `MONITORING_USERNAME` environment variable (default: admin)
- **Password**: Set via `MONITORING_PASSWORD` environment variable (default: chinaxiv2024)

## Endpoints

### Public Endpoints

#### Get Site Index
```http
GET /
```
Returns the main site index page with search functionality.

#### Get Paper Details
```http
GET /items/{paper_id}.html
```
Returns the detailed view of a specific paper.

**Parameters:**
- `paper_id`: The unique identifier of the paper

**Response:** HTML page with paper details, translation, and download links

#### Search Papers
```http
GET /search
```
Client-side search functionality using the search index.

**Parameters:**
- `q`: Search query string

**Response:** JSON array of matching papers

#### Download Files
```http
GET /items/{paper_id}.{format}
```
Download paper files in various formats.

**Parameters:**
- `paper_id`: The unique identifier of the paper
- `format`: File format (`pdf`, `html`, `md`)

**Response:** File download

### Monitoring Endpoints

#### Health Check
```http
GET /monitor/health
```
Returns system health status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-10-05T20:00:00Z",
  "version": "1.0.0"
}
```

#### System Status
```http
GET /monitor/api/system
```
Returns system statistics and status.

**Response:**
```json
{
  "uptime": "Running",
  "last_update": "2025-10-05 20:00:00",
  "site_url": "https://chinarxiv.org",
  "github_actions_status": "Active",
  "cloudflare_status": "Online"
}
```

#### Job Statistics
```http
GET /monitor/api/stats
```
Returns job processing statistics.

**Response:**
```json
{
  "total": 1000,
  "completed": 950,
  "pending": 30,
  "failed": 20,
  "progress_percent": 95.0,
  "estimated_completion": "2025-10-06 10:00:00"
}
```

#### Recent Logs
```http
GET /monitor/api/logs
```
Returns recent system logs.

**Response:**
```json
[
  {
    "timestamp": "2025-10-05 20:00:00",
    "level": "INFO",
    "message": "Translation completed successfully",
    "source": "translation.log"
  }
]
```

#### Alerts
```http
GET /monitor/alerts
```
Returns recent alerts.

**Parameters:**
- `limit`: Maximum number of alerts to return (default: 50)
- `level`: Filter by alert level (`info`, `warning`, `error`, `critical`)

**Response:**
```json
[
  {
    "level": "info",
    "title": "Translation Complete",
    "message": "Successfully translated 10 papers",
    "timestamp": "2025-10-05T20:00:00Z",
    "source": "system",
    "metadata": {}
  }
]
```

#### Create Alert
```http
POST /monitor/alerts/create
```
Creates a new alert.

**Request Body:**
```json
{
  "level": "info",
  "title": "Alert Title",
  "message": "Alert message",
  "source": "api",
  "metadata": {}
}
```

**Response:**
```json
{
  "success": true,
  "alert": {
    "level": "info",
    "title": "Alert Title",
    "message": "Alert message",
    "timestamp": "2025-10-05T20:00:00Z",
    "source": "api",
    "metadata": {}
  }
}
```

#### Analytics
```http
GET /monitor/analytics
```
Returns analytics data.

**Parameters:**
- `days`: Number of days to include (default: 7)

**Response:**
```json
{
  "page_views": [
    {
      "timestamp": "2025-10-05T20:00:00Z",
      "page": "/",
      "user_agent": "Mozilla/5.0...",
      "ip_address": "192.168.1.1",
      "referrer": null,
      "session_id": null
    }
  ],
  "search_queries": [
    {
      "timestamp": "2025-10-05T20:00:00Z",
      "query": "machine learning",
      "results": 25,
      "user_agent": "Mozilla/5.0...",
      "ip_address": "192.168.1.1",
      "session_id": null
    }
  ],
  "period_days": 7,
  "generated_at": "2025-10-05T20:00:00Z"
}
```

#### Performance Metrics
```http
GET /monitor/performance
```
Returns performance metrics.

**Parameters:**
- `days`: Number of days to include (default: 7)

**Response:**
```json
{
  "metrics": [
    {
      "timestamp": "2025-10-05T20:00:00Z",
      "name": "response_time",
      "value": 150.5,
      "unit": "ms",
      "metadata": {}
    }
  ],
  "period_days": 7,
  "generated_at": "2025-10-05T20:00:00Z"
}
```

#### Performance Report
```http
GET /monitor/performance/report
```
Returns comprehensive performance report.

**Parameters:**
- `days`: Number of days to include (default: 7)

**Response:**
```json
{
  "period_days": 7,
  "metrics": {
    "response_time": {
      "count": 100,
      "min": 50.0,
      "max": 500.0,
      "avg": 150.5,
      "median": 140.0
    }
  },
  "trends": {
    "response_time": "stable"
  },
  "recommendations": [
    "Consider optimizing response_time (avg: 150.5ms)"
  ],
  "generated_at": "2025-10-05T20:00:00Z"
}
```

#### Optimize Performance
```http
POST /monitor/performance/optimize
```
Runs performance optimizations.

**Request Body:**
```json
{
  "type": "all"
}
```

**Response:**
```json
{
  "search_index": {
    "success": true,
    "message": "Compressed by 45.2%"
  },
  "images": {
    "success": true,
    "message": "Processed 25 images"
  }
}
```

## Data Models

### Paper
```json
{
  "id": "paper-20241005-001",
  "oai_identifier": "oai:chinaxiv.org:202410.00123",
  "title": "Machine Learning Applications",
  "title_en": "Machine Learning Applications",
  "creators": ["Zhang, Wei", "Li, Ming"],
  "abstract": "This paper presents...",
  "abstract_en": "This paper presents...",
  "subjects": ["cs.AI", "cs.LG"],
  "date": "2024-10-05",
  "pdf_url": "https://example.org/download/paper.pdf",
  "source_url": "https://chinaxiv.org/abs/202410.00123",
  "license": {
    "raw": "CC BY 4.0",
    "derivatives_allowed": true
  },
  "setSpec": "cs.AI"
}
```

### Alert
```json
{
  "level": "info",
  "title": "Alert Title",
  "message": "Alert message",
  "timestamp": "2025-10-05T20:00:00Z",
  "source": "system",
  "metadata": {}
}
```

### Page View
```json
{
  "timestamp": "2025-10-05T20:00:00Z",
  "page": "/",
  "user_agent": "Mozilla/5.0...",
  "ip_address": "192.168.1.1",
  "referrer": null,
  "session_id": null
}
```

### Search Query
```json
{
  "timestamp": "2025-10-05T20:00:00Z",
  "query": "machine learning",
  "results": 25,
  "user_agent": "Mozilla/5.0...",
  "ip_address": "192.168.1.1",
  "session_id": null
}
```

### Performance Metric
```json
{
  "timestamp": "2025-10-05T20:00:00Z",
  "name": "response_time",
  "value": 150.5,
  "unit": "ms",
  "metadata": {}
}
```

## Error Handling

### HTTP Status Codes
- `200 OK`: Request successful
- `400 Bad Request`: Invalid request parameters
- `401 Unauthorized`: Authentication required
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server error

### Error Response Format
```json
{
  "error": "Error message",
  "code": "ERROR_CODE",
  "timestamp": "2025-10-05T20:00:00Z"
}
```

### Common Error Codes
- `INVALID_PARAMETERS`: Invalid request parameters
- `AUTHENTICATION_REQUIRED`: Authentication required
- `RESOURCE_NOT_FOUND`: Resource not found
- `RATE_LIMIT_EXCEEDED`: Rate limit exceeded
- `INTERNAL_ERROR`: Internal server error

## Rate Limiting
- **Public endpoints**: No rate limiting
- **Monitoring endpoints**: 100 requests per minute per IP
- **Alert creation**: 10 requests per minute per IP

## Caching
- **Static content**: Cached by Cloudflare CDN
- **API responses**: Cached for 5 minutes
- **Search index**: Cached for 1 hour

## Security

### HTTPS
All endpoints require HTTPS in production.

### CORS
CORS is enabled for all origins in development, restricted in production.

### Input Validation
All input parameters are validated and sanitized.

### Authentication
Monitoring endpoints require authentication via session cookies.

## Examples

### Search for Papers
```bash
curl "https://chinarxiv.org/search?q=machine%20learning"
```

### Get Paper Details
```bash
curl "https://chinarxiv.org/items/paper-20241005-001.html"
```

### Download PDF
```bash
curl -O "https://chinarxiv.org/items/paper-20241005-001.pdf"
```

### Create Alert
```bash
curl -X POST "https://chinarxiv.org/monitor/alerts/create" \
  -H "Content-Type: application/json" \
  -d '{
    "level": "info",
    "title": "Test Alert",
    "message": "This is a test alert",
    "source": "api"
  }'
```

### Get System Status
```bash
curl "https://chinarxiv.org/monitor/api/system"
```

## SDKs and Libraries

### Python
```python
import requests

# Get paper details
response = requests.get("https://chinarxiv.org/items/paper-20241005-001.html")

# Create alert
response = requests.post(
    "https://chinarxiv.org/monitor/alerts/create",
    json={
        "level": "info",
        "title": "Test Alert",
        "message": "This is a test alert",
        "source": "api"
    }
)
```

### JavaScript
```javascript
// Get paper details
fetch('https://chinarxiv.org/items/paper-20241005-001.html')
  .then(response => response.text())
  .then(html => console.log(html));

// Create alert
fetch('https://chinarxiv.org/monitor/alerts/create', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    level: 'info',
    title: 'Test Alert',
    message: 'This is a test alert',
    source: 'api'
  })
});
```

## Changelog

### Version 1.0.0 (2025-10-05)
- Initial API release
- Public endpoints for papers and search
- Monitoring dashboard with authentication
- Alert system
- Analytics tracking
- Performance monitoring

## Support
- **Documentation**: [GitHub Repository](https://github.com/your-org/chinaxiv-english)
- **Issues**: [GitHub Issues](https://github.com/your-org/chinaxiv-english/issues)
- **Contact**: [Email](mailto:support@chinaxiv-english.com)
