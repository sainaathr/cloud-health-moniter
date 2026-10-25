import requests
import json
import time

print("=== 1. CLUSTER OVERVIEW API ===")
r = requests.get("http://127.0.0.1:5000/api/cluster/overview", timeout=10)
data = r.json()
print("Status:", r.status_code)
print("Data Source:", data.get("data_source"))
print("App Health:", data.get("app_health"))
print("K8s Connected:", data.get("k8s_connected"))
print("Controller Status:", data.get("controller_status"))

print("\n=== 2. CLUSTER PODS API ===")
r = requests.get("http://127.0.0.1:5000/api/cluster/pods", timeout=10)
pods_data = r.json()
print("Status:", r.status_code)
print("Source:", pods_data.get("source"))
print("Pod count:", pods_data.get("count"))
for p in pods_data.get("pods", []):
    print(f"  - {p['name']} ({p['workload']}): Phase={p['phase']}, Ready={p['ready']}, Restarts={p['restarts']}")

print("\n=== 3. READINESS TOGGLE & HEALTH CHECK ===")
r1 = requests.post("http://127.0.0.1:5000/api/chaos/readiness", timeout=5)
print("Toggle to unready:", r1.json().get("message"))
r2 = requests.get("http://127.0.0.1:5000/health/ready", timeout=5)
print("/health/ready status code:", r2.status_code, "(Expected: 503)")

print("\n=== 4. INCIDENT TIMELINE IN SQLITE ===")
r3 = requests.get("http://127.0.0.1:5000/api/incidents?limit=5", timeout=5)
incs = r3.json().get("incidents", [])
print(f"Total incidents returned: {len(incs)}")
for i in incs:
    print(f"  - [{i['incident_id']}] {i['incident_type']} | Status: {i['status']} | Pod: {i['target_pod']}")

print("\n=== 5. RESET FAULTS ===")
r4 = requests.post("http://127.0.0.1:5000/api/chaos/reset", timeout=5)
print("Reset response:", r4.json().get("message"))
r5 = requests.get("http://127.0.0.1:5000/health/ready", timeout=5)
print("/health/ready status code after reset:", r5.status_code, "(Expected: 200)")

print("\n=== 6. CONTROLLED MEMORY ALLOCATION ===")
r_mem = requests.get("http://127.0.0.1:5000/api/memory-test?size_mb=20", timeout=5)
print("Memory allocation:", r_mem.json())
r_clear = requests.get("http://127.0.0.1:5000/api/memory-test?size_mb=0", timeout=5)
print("Memory cleared:", r_clear.json())

print("\n=== 7. PROMETHEUS METRICS SCRAPE ===")
r6 = requests.get("http://127.0.0.1:5000/metrics", timeout=5)
print("Metrics status:", r6.status_code, "| Length:", len(r6.text))
has_reqs = "http_requests_total" in r6.text
has_dur = "http_request_duration_seconds" in r6.text
print("Contains http_requests_total:", has_reqs, "| Contains duration:", has_dur)

print("\n=== 8. PROMETHEUS & GRAFANA REACHABILITY ===")
for name, url in [("Prometheus", "http://localhost:9090"), ("Grafana", "http://localhost:3000")]:
    try:
        res = requests.get(url, timeout=1.5)
        print(f"{name} ({url}): Accessible (Status {res.status_code})")
    except Exception as e:
        print(f"{name} ({url}): Not reachable ({type(e).__name__})")
