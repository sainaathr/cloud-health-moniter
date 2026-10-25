import requests
import json
import time

queries = [
    ("1. HTTP Request Rate", 'sum(rate(http_requests_total{service="sample-app-service"}[2m])) by (endpoint, status_code)'),
    ("2. Latency", '(sum(rate(http_request_duration_seconds_sum{service="sample-app-service"}[2m])) by (endpoint) / sum(rate(http_request_duration_seconds_count{service="sample-app-service"}[2m])) by (endpoint))'),
    ("3. HTTP Errors (5xx)", '(sum(rate(http_requests_total{status_code=~"5..", service="sample-app-service"}[2m])) by (endpoint, status_code)) or (sum(rate(http_requests_total{service="sample-app-service"}[2m])) * 0)')
]

now = time.time()
for name, q in queries:
    res = requests.get("http://127.0.0.1:32090/api/v1/query_range", params={
        "query": q,
        "start": now - 1800, # last 30 minutes
        "end": now,
        "step": 30
    }).json()
    print("=" * 60)
    print(name)
    results = res.get("data", {}).get("result", [])
    print(f"Time series count: {len(results)}")
    for r in results:
        vals = r.get("values", [])
        non_zero = [v for v in vals if float(v[1]) > 0]
        print(f"  Series {r.get('metric')}: {len(vals)} points, {len(non_zero)} non-zero")
