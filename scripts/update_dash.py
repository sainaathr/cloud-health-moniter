import json
import os
import urllib.request
import base64

# Locate dashboard file dynamically relative to this script directory or via env var
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
default_dashboard = os.path.join(BASE_DIR, "dashboards", "grafana-dashboard.json")
dashboard_path = os.getenv("GRAFANA_DASHBOARD_PATH", default_dashboard)

if not os.path.exists(dashboard_path):
    print(f"Error: Dashboard file not found at: {dashboard_path}")
    exit(1)

with open(dashboard_path, "r", encoding="utf-8") as f:
    dashboard = json.load(f)

# Find the maximum panel ID to avoid conflicts
max_id = 0
for panel in dashboard.get("panels", []):
    if panel.get("id", 0) > max_id:
        max_id = panel["id"]

def next_id():
    global max_id
    max_id += 1
    return max_id

# New Row
row_panel = {
  "collapsed": False,
  "gridPos": { "h": 1, "w": 24, "x": 0, "y": 32 },
  "id": next_id(),
  "title": "🌐 Application Metrics (Flask)",
  "type": "row"
}

# Request Rate (Requests per sec)
req_rate_panel = {
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "fieldConfig": { "defaults": { "custom": { "lineWidth": 2 } } },
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 33 },
  "id": next_id(),
  "title": "HTTP Request Rate",
  "type": "timeseries",
  "targets": [
    {
      "expr": "sum(rate(http_requests_total{service=\"sample-app-service\"}[5m])) by (endpoint, status_code) or sum(rate(http_requests_total{app=\"sample-app\"}[5m])) by (endpoint, status_code)",
      "legendFormat": "{{ endpoint }} ({{ status_code }})"
    }
  ]
}

# Average Latency
latency_panel = {
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "fieldConfig": { "defaults": { "unit": "s", "custom": { "lineWidth": 2 } } },
  "gridPos": { "h": 8, "w": 12, "x": 12, "y": 33 },
  "id": next_id(),
  "title": "HTTP Request Average Latency",
  "type": "timeseries",
  "targets": [
    {
      "expr": "(sum(rate(http_request_duration_seconds_sum{service=\"sample-app-service\"}[5m])) by (endpoint) / sum(rate(http_request_duration_seconds_count{service=\"sample-app-service\"}[5m])) by (endpoint)) > 0",
      "legendFormat": "{{ endpoint }} Latency"
    }
  ]
}

# Memory Allocations
mem_panel = {
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "fieldConfig": { "defaults": { "unit": "bytes", "custom": { "lineWidth": 2 } } },
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 41 },
  "id": next_id(),
  "title": "Application Allocated Memory (Bounded Leak)",
  "type": "timeseries",
  "targets": [
    {
      "expr": "sum(app_memory_allocated_bytes{service=\"sample-app-service\"}) by (instance) or sum(app_memory_allocated_bytes) by (instance)",
      "legendFormat": "{{ instance }} Memory"
    },
    {
      "expr": "sum(app_memory_allocated_bytes{service=\"sample-app-service\"}) or sum(app_memory_allocated_bytes)",
      "legendFormat": "Total Bounded Leak"
    }
  ]
}

# HTTP Errors (5xx)
err_panel = {
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "fieldConfig": { "defaults": { "custom": { "lineWidth": 2 } } },
  "gridPos": { "h": 8, "w": 12, "x": 12, "y": 41 },
  "id": next_id(),
  "title": "HTTP Errors (5xx)",
  "type": "timeseries",
  "targets": [
    {
      "expr": "sum(rate(http_requests_total{status_code=~\"5..\", service=\"sample-app-service\"}[5m])) by (endpoint, status_code) or vector(0)",
      "legendFormat": "{{ endpoint }} - {{ status_code }}"
    }
  ]
}

titles_to_remove = [
    "🌐 Application Metrics (Flask)",
    "HTTP Request Rate",
    "HTTP Request Average Latency",
    "Application Allocated Memory (Bounded Leak)",
    "HTTP Errors (5xx)"
]
dashboard["panels"] = [p for p in dashboard.get("panels", []) if p.get("title") not in titles_to_remove]
dashboard["panels"].extend([row_panel, req_rate_panel, latency_panel, mem_panel, err_panel])

with open(dashboard_path, "w", encoding="utf-8") as f:
    json.dump(dashboard, f, indent=2)

print(f"Dashboard JSON successfully updated locally: {dashboard_path}")

grafana_url = os.getenv("GRAFANA_URL", "http://localhost:3000").rstrip("/")
grafana_user = os.getenv("GRAFANA_USER", "admin")
grafana_password = os.getenv("GRAFANA_PASSWORD", "admin123")

endpoint = f"{grafana_url}/api/dashboards/db"
payload = json.dumps({"dashboard": dashboard, "overwrite": True}).encode('utf-8')
auth_header = 'Basic ' + base64.b64encode(f"{grafana_user}:{grafana_password}".encode('utf-8')).decode('utf-8')

req = urllib.request.Request(endpoint, data=payload, headers={
    'Content-Type': 'application/json',
    'Authorization': auth_header
})

try:
    with urllib.request.urlopen(req, timeout=3) as response:
        print("Grafana API Push Success:", response.read().decode('utf-8'))
except Exception as e:
    msg = getattr(e, 'read', lambda: b'Connection refused or unavailable')().decode('utf-8') if hasattr(e, 'read') else str(e)
    print(f"Note: Live Grafana API push skipped/unavailable ({msg}). Dashboard file is saved locally.")
