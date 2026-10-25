import requests
import time

now = time.time()
panels = [
    ("Rate", 'sum(rate(http_requests_total{service="sample-app-service"}[2m])) by (endpoint, status_code)'),
    ("Latency", '(sum(rate(http_request_duration_seconds_sum{service="sample-app-service"}[2m])) by (endpoint) / sum(rate(http_request_duration_seconds_count{service="sample-app-service"}[2m])) by (endpoint))'),
    ("Memory", 'sum(app_memory_allocated_bytes{service="sample-app-service"}) by (instance)'),
    ("5xx", 'sum(rate(http_requests_total{status_code=~"5..", service="sample-app-service"}[1m])) or vector(0)')
]

for name, q in panels:
    res = requests.get("http://127.0.0.1:9090/api/v1/query_range", params={
        "query": q,
        "start": now - 300,
        "end": now,
        "step": 15
    }).json()
    series = res.get("data", {}).get("result", [])
    print(f"{name}: {len(series)} series found")
    for s in series[:3]:
        vals = s.get("values", [])
        print(f"  {s.get('metric')}: {len(vals)} points, last val: {vals[-1] if vals else 'empty'}")
