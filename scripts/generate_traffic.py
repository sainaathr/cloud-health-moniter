import argparse
import requests
import time
import concurrent.futures
import threading

# Traffic Configuration
URL = "http://127.0.0.1:30080" # Default minikube tunnel or port-forward URL, but we will accept this as arg
success_count = 0
error_count = 0
latencies = []
lock = threading.Lock()

def make_request(url, endpoint, params=None):
    global success_count, error_count, latencies
    start_time = time.time()
    try:
        response = requests.get(f"{url}{endpoint}", params=params, timeout=5)
        latency = time.time() - start_time
        
        with lock:
            if response.status_code == 200:
                success_count += 1
                latencies.append(latency)
            else:
                error_count += 1
    except requests.RequestException:
        with lock:
            error_count += 1

def worker_loop(url, endpoint, params, delay, stop_event):
    while not stop_event.is_set():
        make_request(url, endpoint, params)
        time.sleep(delay)

def main():
    parser = argparse.ArgumentParser(description="Realistic HTTP Traffic Generator")
    parser.add_argument("--url", default="http://127.0.0.1:30080", help="Base URL of application")
    parser.add_argument("--workers", type=int, default=5, help="Number of concurrent workers")
    parser.add_argument("--duration", type=int, default=60, help="Duration to run traffic (seconds)")
    parser.add_argument("--complexity", type=int, default=500000, help="CPU work complexity")
    parser.add_argument("--ramp-up", type=int, default=0, help="Seconds to stagger worker starts")
    
    args = parser.parse_args()
    stop_event = threading.Event()
    
    print(f"Starting traffic generator -> {args.url}")
    print(f"Workers: {args.workers}, Duration: {args.duration}s, CPU Complexity: {args.complexity}")
    
    if args.url == "http://127.0.0.1:30080":
        print("Note: Using default URL. If this fails, run 'minikube service sample-app-service -n health-monitor --url' to get the actual URL.")

    # Pre-flight check
    try:
        res = requests.get(f"{args.url}/health/live", timeout=3)
        if res.status_code != 200:
            print(f"Error: /health/live returned {res.status_code}. Application might be down. Aborting.")
            return
    except Exception as e:
        print(f"Error connecting to base URL ({args.url}): {e}")
        print("Hint: Obtain the correct URL by running 'minikube service sample-app-service -n health-monitor --url'")
        return

    threads = []
    
    for i in range(args.workers):
        t = threading.Thread(
            target=worker_loop, 
            args=(args.url, "/api/work", {"complexity": args.complexity}, 0.5, stop_event) # delay 0.5s between reqs per worker
        )
        threads.append(t)
        t.start()
        if args.ramp_up > 0:
            time.sleep(args.ramp_up / args.workers)

    print("Generation started. Press Ctrl+C to stop manually.")
    
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        print("\nStopping traffic...")
        
    stop_event.set()
    for t in threads:
        t.join()
        
    print("\n=== Traffic Generation Results ===")
    print(f"Successful Requests: {success_count}")
    print(f"Failed Requests: {error_count}")
    if len(latencies) > 0:
        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[int(len(latencies)*0.95)]
        print(f"Average Latency: {avg_latency*1000:.2f} ms")
        print(f"P95 Latency: {p95_latency*1000:.2f} ms")
    else:
        print("No successful requests recorded.")

if __name__ == "__main__":
    main()
