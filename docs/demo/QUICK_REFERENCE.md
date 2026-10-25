# QUICK REFERENCE & DEMO CHEAT SHEET
## Autonomous Kubernetes Health Monitor & Self-Healing Control Plane v2.0
**Executive Summary & Presentation Pocket Card**

---

### 1. Essential Architecture & Topology

- **Namespace**: `health-monitor`
- **Application Deployment**: `sample-app` (2 replicas, CPU request: `50m`, limit: `200m`, Memory request: `64Mi`, limit: `128Mi`)
- **Controller Deployment**: `health-monitor-controller` (1 replica, polling interval: 30s, self-protected)
- **Application Service**: `sample-app-service` (NodePort: 30080, ClusterIP: Port 80, Container: Port 5000)
- **HPA**: `sample-app-hpa` (min: 2, max: 6, CPU target: 60% relative to 50m request)
- **Incident Database**: SQLite at `/app/data/incidents.db` mounted on PVC `incident-data-pvc` (128Mi)
- **Email Delivery**: SMTP via `smtp.gmail.com:587` (STARTTLS), recipient: `sainaath.r2025@vitstudent.ac.in`

---

### 2. Key Ports & Dashboard URLs

| Service | Local URL | In-Cluster Port / Target | Purpose |
| :--- | :--- | :--- | :--- |
| **Web Dashboard** | `http://localhost:5000` | Port 5000 (Flask / Jinja2 / Tailwind) | Main control plane & chaos dashboard |
| **Service NodePort** | `http://localhost:30080` | Port 80 $\rightarrow$ TargetPort 5000 | Direct cluster NodePort endpoint |
| **Prometheus Server** | `http://localhost:9090` | Port 9090 (v2.x TSDB) | PromQL time-series metric exploration |
| **Grafana Dashboards** | `http://localhost:3000` | Port 80 (Admin: `admin` / `prom-operator`) | Cluster & application metric visualization |
| **Application Metrics** | `http://localhost:5000/metrics` | Port 5000 (`/metrics`) | Raw Prometheus formatted metric scrape |

---

### 3. Five Most Important Commands to Memorize

```powershell
# 1. Check all cluster workloads at a glance
kubectl get pods,svc,hpa -n health-monitor -o wide

# 2. Check real-time CPU and Memory consumption (independent verification)
kubectl top pods -n health-monitor

# 3. Check service endpoints (proves readiness traffic isolation)
kubectl get endpoints sample-app-service -n health-monitor

# 4. View autonomous controller logs (proves autonomous healing loop)
kubectl logs deployment/health-monitor-controller -n health-monitor --tail=25

# 5. Check email notification delivery audit from persistent store
curl.exe -s http://localhost:5000/api/notifications/history?limit=3
```

---

### 4. 12–15 Minute Proposed Demo Sequence

```text
[00:00 - 02:00]  1. INTRO & BASELINE
                 • Open http://localhost:5000.
                 • Show "Live Kubernetes API" badge and Serving Host.
                 • Run: kubectl get pods,svc,hpa -n health-monitor.
                 • Point: 3 pods running, 1/1 Ready, 0 restarts.

[02:00 - 04:30]  2. REAL CPU WORKLOAD & METRICS
                 • Click "1M CPU Workload", then "Traffic Burst (10s)".
                 • Run: kubectl top pods -n health-monitor.
                 • Point: CPU spikes from 1m to 45m+ (genuine math computation).
                 • Show Prometheus /metrics counters incrementing.

[04:30 - 06:30]  3. BOUNDED MEMORY PRESSURE
                 • Click "Allocate +20MB" twice (reaches 40MB).
                 • Click a 3rd time: show 50MB safety ceiling rejection (HTTP 400).
                 • Click "Clear Memory (0MB)": show reset to 0 bytes in /api/status.

[06:30 - 08:30]  4. READINESS PROBE ISOLATION
                 • Click "Toggle HTTP 503".
                 • Run: curl.exe -i http://localhost:5000/health/ready (returns 503).
                 • Run: kubectl get endpoints sample-app-service (pod IP removed).
                 • Run: kubectl get pods (shows 0/1 Ready, but still Running).
                 • Click toggle again: restored to 200 OK.

[08:30 - 11:00]  5. DESTRUCTIVE CRASH & KUBELET RESTART
                 • Show Admin Token (k8s-chaos-admin-secure) and Dry-Run mode.
                 • Test Dry-Run: validates token without killing pod.
                 • Uncheck Dry-Run, click "Pod Crash Injection".
                 • Process terminates via os._exit(1); Kubelet restarts container.
                 • Run: kubectl get pods (RESTARTS increments to 1).

[11:00 - 13:00]  6. AUTONOMOUS CONTROLLER & HEALING
                 • Run: kubectl logs deployment/health-monitor-controller --tail=30.
                 • Explain 30s poll loop, self-protection, and Deployment resolution.
                 • Explain _wait_for_replacement(): verifies replacement is Ready.

[13:00 - 14:30]  7. REAL EMAIL NOTIFICATION
                 • Show Notification Center card: "LIVE SMTP READY".
                 • Show masked credentials: sa***@gmail.com, STARTTLS on port 587.
                 • Click "Demo: Send Real Email".
                 • Audit table logs: status smtp_accepted to university email.

[14:30 - 15:00]  8. RESET & HEALTHY BASELINE
                 • Click "↺ Reset All Faults".
                 • Verify clean baseline: 0MB memory, 1/1 Ready, 0 pending incidents.
```

---

### 5. Five Most Likely Viva Questions & Instant Answers

1. **"How do I know this isn't faked or simulated?"**
   - *Answer*: *"Every metric is independently verified via `kubectl top pods`, Prometheus `/metrics`, and Kubelet probes. When Minikube is disconnected, the dashboard immediately reports UNAVAILABLE and turns red. No fake pods are fabricated."*

2. **"What is the difference between a Readiness failure and a Liveness failure?"**
   - *Answer*: *"Readiness failures remove the pod's IP from Service endpoints without killing the process, protecting users from temporary unready states. Liveness failures cause Kubelet to terminate and restart the container."*

3. **"Why does the controller delete the pod instead of restarting it?"**
   - *Answer*: *"Deleting the pod triggers the Kubernetes Deployment controller to schedule a clean replacement pod with a new UID, new IP, and fresh memory space. Container restarts in-place are handled by Kubelet; the controller handles stuck, unrecoverable pods."*

4. **"How does the controller avoid infinite deletion loops?"**
   - *Answer*: *"It enforces a 60-second cooldown timer between attempts on the same pod and caps retries at 3 attempts (`MAX_RECOVERY_ATTEMPTS`). After 3 attempts, it aborts auto-healing and alerts human operators."*

5. **"Why does the email audit show `SMTP ACCEPTED` instead of 'Inbox Delivered'?"**
   - *Answer*: *"Because SMTP is a store-and-forward protocol. `SMTP ACCEPTED` proves the relay server (`smtp.gmail.com`) accepted custody of the message with response 250 OK. Inbox delivery is handled downstream by destination mail filtering rules."*

---

### 6. Exact Source Files to Study First

1. **[app/controller/health_monitor.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py)**: Study `evaluate_pod()` (line 231), `get_pod_owner_deployment()` (line 309), `attempt_recovery()` (line 338), and `_wait_for_replacement()` (line 536).
2. **[app/ui/app.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py)**: Study `_check_destructive_authorization()` (line 75), `api_work()` (line 137), `memory_test()` (line 149), `toggle_readiness()` (line 231), and `crash()` (line 329).
3. **[app/notifications.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py)**: Study `get_masked_config()` (line 261) and `_is_deduplicated()` (line 334).
4. **[k8s/deployment.yaml](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml)**: Study resource limits (lines 78–84) and probe definitions (lines 87–102).
5. **[k8s/role.yaml](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/role.yaml)**: Study least-privilege RBAC rules (lines 20–39).

---

### 7. Pre-Demo Safety Checklist

- [ ] Docker Desktop running with active WSL2 backend.
- [ ] Minikube running: `minikube status` shows `host: Running`, `kubelet: Running`.
- [ ] Port forward daemon active: `py start_port_forwards.py`.
- [ ] Browser open to `http://localhost:5000` showing green `Live Kubernetes API` badge.
- [ ] Kubernetes Secret `smtp-credentials` verified: status shows `LIVE SMTP READY`.
- [ ] PowerShell terminal open side-by-side with browser, positioned at workspace root.
- [ ] Clean baseline confirmed: run `curl.exe -s -X POST http://localhost:5000/api/chaos/reset`.
