import requests
import time
import concurrent.futures

BASE_URLS = ["http://127.0.0.1:30080", "http://127.0.0.1:5000"]

def send_traffic(url, endpoint, count=20):
    for _ in range(count):
        try:
            requests.get(f"{url}{endpoint}", timeout=2)
        except Exception:
            pass

print("1. Generating normal traffic across endpoints...")
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    for base in BASE_URLS:
        executor.submit(send_traffic, base, "/api/work?complexity=50000", 30)
        executor.submit(send_traffic, base, "/api/work?complexity=200000", 20)
        executor.submit(send_traffic, base, "/api/status", 40)
        executor.submit(send_traffic, base, "/", 30)

print("2. Toggling readiness to simulate 503 errors on in-cluster pods...")
for base in BASE_URLS:
    try:
        r = requests.post(f"{base}/api/chaos/readiness", timeout=3)
        print(f"Readiness toggled on {base}: {r.json()}")
    except Exception as e:
        print(f"Error toggling readiness on {base}: {e}")

print("3. Generating HTTP 503 responses...")
for base in BASE_URLS:
    for _ in range(15):
        try:
            r = requests.get(f"{base}/health/ready", timeout=2)
            print(f"Status from {base}/health/ready: {r.status_code}")
        except Exception as e:
            pass

print("4. Restoring readiness back to healthy state...")
for base in BASE_URLS:
    try:
        r = requests.post(f"{base}/api/chaos/reset", timeout=3)
        print(f"Reset {base}: {r.json().get('message')}")
    except Exception as e:
        print(f"Error resetting {base}: {e}")

print("5. Generating follow-up healthy traffic...")
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    for base in BASE_URLS:
        executor.submit(send_traffic, base, "/api/work?complexity=10000", 20)
        executor.submit(send_traffic, base, "/health/ready", 20)

print("Traffic and 5xx error generation complete!")
