import subprocess
import time
import threading
import sys

def run_pf(cmd):
    while True:
        print(f"Starting: {cmd}", flush=True)
        # We explicitly use sys.executable to ensure we don't block on shell inheritance, though shell=True works well.
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.wait()
        print(f"Connection dropped: {cmd}. Restarting in 2 seconds...", flush=True)
        time.sleep(2)

cmds = [
    "kubectl port-forward --address 127.0.0.1 svc/sample-app-service 5000:80 -n health-monitor",
    "kubectl port-forward --address 127.0.0.1 svc/sample-app-service 30080:80 -n health-monitor",
    "kubectl port-forward --address 127.0.0.1 svc/grafana 3000:80 -n monitoring",
    "kubectl port-forward --address 127.0.0.1 svc/grafana 32000:80 -n monitoring",
    "kubectl port-forward --address 127.0.0.1 svc/prometheus-server 9090:80 -n monitoring",
    "kubectl port-forward --address 127.0.0.1 svc/prometheus-server 32090:80 -n monitoring"
]

print("Launching persistent port forwards...", flush=True)

threads = []
for c in cmds:
    t = threading.Thread(target=run_pf, args=(c,))
    t.daemon = True
    t.start()
    threads.append(t)

# Keep main thread alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Exiting...")
