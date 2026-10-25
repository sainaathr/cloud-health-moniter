import requests
import json

queries = [
    ("1. HTTP Request Rate", "sum(rate(http_requests_total{service=\"sample-app-service\"}[5m])) by (endpoint, status_code)"),
    ("2. HTTP Request Average Latency", "(sum(rate(http_request_duration_seconds_sum{service=\"sample-app-service\"}[5m])) by (endpoint) / sum(rate(http_request_duration_seconds_count{service=\"sample-app-service\"}[5m])) by (endpoint))"),
    ("3. Application Allocated Memory (Bounded)", "sum(app_memory_allocated_bytes{service=\"sample-app-service\"}) by (pod)"),
    ("4. HTTP Errors (5xx)", "sum(rate(http_requests_total{status_code=~\"5..\", service=\"sample-app-service\"}[5m])) by (endpoint, status_code)"),
    ("5. Raw Counter Total", "sum(http_requests_total{service=\"sample-app-service\"}) by (endpoint, status_code)")
]

print("=== VERIFYING PROMETHEUS METRICS FOR GRAFANA PANELS ===")
for title, q in queries:
    r = requests.get("http://127.0.0.1:9090/api/v1/query", params={"query": q}, timeout=5)
    data = r.json().get("data", {}).get("result", [])
    print(f"\n{title}:")
    print(f"  Expr: {q}")
    print(f"  Found {len(data)} time series series:")
    for item in data:
        labels = item.get("metric", {})
        val = item.get("value", [None, None])[1]
        print(f"    - Labels: {labels} => Value: {val}")
