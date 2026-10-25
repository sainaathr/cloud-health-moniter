import requests
import json
import subprocess

print("=== 1. PROMETHEUS SCRAPE TARGETS ===")
try:
    r = requests.get("http://127.0.0.1:9090/api/v1/targets", timeout=5)
    targets = r.json().get("data", {}).get("activeTargets", [])
    print(f"Total active targets: {len(targets)}")
    for t in targets:
        job = t.get("labels", {}).get("job")
        url = t.get("scrapeUrl")
        health = t.get("health")
        err = t.get("lastError")
        print(f"  Target [{health}]: {job} -> {url} (Error: {err or 'None'})")
except Exception as e:
    print(f"Failed to query Prometheus targets: {e}")

print("\n=== 2. PROMETHEUS METRIC QUERIES ===")
queries = [
    "http_requests_total",
    "http_request_duration_seconds_count",
    "app_allocated_memory_bytes",
    "http_requests_total{status=~\"5..\"}",
    "up"
]
for q in queries:
    try:
        r = requests.get("http://127.0.0.1:9090/api/v1/query", params={"query": q}, timeout=5)
        res = r.json().get("data", {}).get("result", [])
        print(f"Query '{q}': {len(res)} results returned")
        if res:
            print(f"   Sample: {res[0].get('metric')} => {res[0].get('value')}")
    except Exception as e:
        print(f"Query '{q}' failed: {e}")

print("\n=== 3. KUBERNETES SERVICE & SERVICEMONITOR CHECK ===")
try:
    out = subprocess.check_output(["kubectl", "get", "servicemonitor", "-A"], text=True)
    print(out)
except Exception as e:
    print(f"ServiceMonitor check failed: {e}")

print("\n=== 4. SAMPLE-APP POD LOGS & PORTS ===")
try:
    out = subprocess.check_output(["kubectl", "get", "pods", "-n", "health-monitor", "-o", "wide"], text=True)
    print(out)
except Exception as e:
    print(f"Pods query failed: {e}")
