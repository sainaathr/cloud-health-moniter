# LIVE DEMONSTRATION TROUBLESHOOTING & RECOVERY RUNBOOK
## Autonomous Kubernetes Health Monitor & Self-Healing Control Plane v2.0
**Emergency Triage & Diagnostic Playbook**

---

### Triage Philosophy
If an unexpected error occurs during a live demonstration before the faculty panel:
1. **Never Panic**: System issues happen in real DevOps environments. Explaining the issue calmly and fixing it with diagnostic commands impresses professors far more than a slick demo that never encounters a glitch.
2. **Never Delete the Cluster**: Do not run `minikube delete` or reinstall monitoring during a presentation.
3. **Use the Matrix Below**: Locate the symptom, run the exact diagnostic command, apply the minimal safe fix, or use the recommended verbal pivot.

---

## Troubleshooting Decision Matrix (19 Scenarios)

### 1. Minikube is Stopped or Unreachable
- **Symptom**: Dashboard shows `Cluster: Kubernetes API Unavailable` in red; `kubectl` commands return `connection refused`.
- **Likely Causes**: Laptop sleep/hibernation, Docker Desktop backend stopped, or Minikube process crashed.
- **Diagnostic Command**:
  ```powershell
  minikube status
  docker ps
  ```
- **Safe Corrective Action**:
  ```powershell
  minikube start --driver=docker
  # Relaunch port forwards after start
  py start_port_forwards.py
  ```
- **When to Skip**: If Minikube takes more than 60 seconds to pull components, pivot to source code review while it starts.
- **Verbal Pivot**: *"Notice that our dashboard immediately detected that the Kubernetes API server was unreachable, truthfully reporting UNAVAILABLE and turning off the live indicators. While the Minikube control plane finishes re-authenticating, let me walk you through our controller architecture in `app/controller/health_monitor.py`."*

---

### 2. A Pod is in `NotReady` or `0/1` State Unexpectedly
- **Symptom**: Pod list shows `sample-app-xxxxx` with `0/1 Ready` before any fault is triggered.
- **Likely Causes**: Readiness probe toggle was left in `HTTP 503` state from a prior test.
- **Diagnostic Command**:
  ```powershell
  curl.exe -i http://localhost:5000/health/ready
  kubectl describe pod -n health-monitor -l app=sample-app | Select-String "Readiness" -Context 0,3
  ```
- **Safe Corrective Action**: Click **↺ Reset All Faults** on the navbar, or run:
  ```powershell
  curl.exe -s -X POST http://localhost:5000/api/chaos/reset
  ```
- **When to Skip**: If pod is CrashLooping due to bad image, delete the bad pod: `kubectl delete pod <bad-pod> -n health-monitor`.

---

### 3. Health Controller Shows `OFFLINE` or `UNAVAILABLE`
- **Symptom**: System Health Card 2 shows `Controller State: UNAVAILABLE`.
- **Likely Causes**: Controller pod was deleted, or container crashed due to missing secret.
- **Diagnostic Command**:
  ```powershell
  kubectl get pods -n health-monitor -l app=health-monitor
  kubectl logs -n health-monitor deployment/health-monitor-controller --tail=20
  ```
- **Safe Corrective Action**:
  ```powershell
  kubectl rollout restart deployment/health-monitor-controller -n health-monitor
  ```
- **Fallback Evidence**: Show previous controller logs preserved in `/app/logs/health-monitor.log` or screenshot in `docs/screenshots/`.

---

### 4. Metrics API is Unavailable (`kubectl top pods` fails)
- **Symptom**: `kubectl top pods` outputs `error: Metrics API not available`.
- **Likely Causes**: Metrics Server addon is disabled or in startup delay.
- **Diagnostic Command**:
  ```powershell
  kubectl get apiservices | Select-String "v1beta1.metrics.k8s.io"
  kubectl get pods -n kube-system -l k8s-app=metrics-server
  ```
- **Safe Corrective Action**:
  ```powershell
  minikube addons enable metrics-server
  # Wait 30 seconds for initial aggregation
  ```
- **When to Skip**: If Metrics Server takes time to collect the first 15-second window, show real metrics directly from the application's `/metrics` endpoint.

---

### 5. HPA Metrics Show `<unknown>/60%` or "HPA resource not queried"
- **Symptom**: HPA card displays `HPA resource not queried` or `kubectl get hpa` shows `cpu: <unknown>/60%`.
- **Likely Causes**:
  1. ClusterRole lacks `autoscaling` RBAC permission (UI reports "not queried").
  2. Metrics Server has not finished calculating the 60-second window average (`<unknown>`).
- **Diagnostic Command**:
  ```powershell
  kubectl get hpa sample-app-hpa -n health-monitor
  ```
- **Safe Corrective Action**: Generate traffic via `Traffic Burst` and wait 20 seconds. If checking RBAC, explain that `kubectl` has cluster-admin rights while the in-cluster UI runs under least-privilege RBAC.
- **Verbal Pivot**: *"Notice this exact behavior: because our in-cluster service account operates under strict least-privilege, the UI truthfully states 'HPA resource not queried' rather than faking data. From the cluster admin CLI, `kubectl get hpa` confirms that HPA is actively evaluating target utilization at 60%."*

---

### 6. CPU Usage Does Not Increase After "1M CPU Workload"
- **Symptom**: `kubectl top pods` does not show a CPU increase.
- **Likely Causes**: The 1M loop completed in under 500ms before the Metrics Server's 15-second polling interval sampled the cgroup.
- **Diagnostic Command**:
  ```powershell
  # Trigger a sustained burst
  curl.exe -s "http://localhost:5000/api/traffic/generate?duration=15&workers=8"
  kubectl top pods -n health-monitor
  ```
- **Safe Corrective Action**: Click **Traffic Burst (10s)** instead of 1M CPU, which maintains sustained load across 5 worker threads.

---

### 7. Traffic Burst Does Not Produce Observable Load
- **Symptom**: UI Action Log says traffic burst started, but metrics do not climb.
- **Likely Causes**: Traffic worker URLs defaulted to `localhost:5000` inside a container where `localhost` points to the container itself rather than the service.
- **Diagnostic Command**: Check `http://localhost:5000/metrics` for `http_requests_total`.
- **Safe Corrective Action**: Run traffic generator script from PowerShell:
  ```powershell
  py scripts/generate_traffic.py
  ```

---

### 8. Memory Usage Does Not Change on "Allocate +20MB"
- **Symptom**: UI counter does not update or stays at 0MB.
- **Likely Causes**: Browser cache or session routing to alternate pod replica.
- **Diagnostic Command**:
  ```powershell
  curl.exe -s http://localhost:5000/api/status
  ```
- **Safe Corrective Action**: Hard refresh browser (`Ctrl + F5`) or click **Reset All Faults** and re-allocate.

---

### 9. Clear Memory Does Not Immediately Reduce OS Memory (RSS)
- **Symptom**: Professor points out that `kubectl top pods` still shows memory above baseline after clicking Clear Memory.
- **Likely Causes**: Normal Python heap behavior (`pymalloc` arena retention).
- **Diagnostic Command**:
  ```powershell
  curl.exe -s http://localhost:5000/api/status
  ```
- **Verbal Pivot**: *"That is a well-documented behavior of the Python virtual machine and glibc allocator. As verified by `/api/status`, the application's allocated byte references are cleared to zero. However, the Python allocator does not immediately return free heap arenas to the Linux OS kernel until subsequent allocations or kernel memory pressure demands it."*

---

### 10. Readiness State Does Not Change in Browser
- **Symptom**: Clicking Toggle HTTP 503 does not turn the badge red.
- **Likely Causes**: JavaScript polling paused or modal error blocked execution.
- **Diagnostic Command**:
  ```powershell
  curl.exe -i http://localhost:5000/health/ready
  ```
- **Safe Corrective Action**: Trigger toggle via PowerShell:
  ```powershell
  curl.exe -s -X POST http://localhost:5000/api/chaos/readiness
  ```
  Refresh the browser window.

---

### 11. Pod Does Not Get Replaced After Deletion
- **Symptom**: Unhealthy pod deleted, but no replacement appears.
- **Likely Causes**: Deployment replicas set to 0, or node has zero allocatable capacity.
- **Diagnostic Command**:
  ```powershell
  kubectl get deployment sample-app -n health-monitor
  kubectl get events -n health-monitor --sort-by='.metadata.creationTimestamp'
  ```
- **Safe Corrective Action**: Ensure desired replicas is 2:
  ```powershell
  kubectl scale deployment sample-app --replicas=2 -n health-monitor
  ```

---

### 12. Container Restart Count Does Not Increment on Crash
- **Symptom**: Crash button clicked, but restart count stays 0.
- **Likely Causes**: Dry-Run mode was checked, or token was invalid.
- **Diagnostic Command**:
  ```powershell
  kubectl get pods -n health-monitor -l app=sample-app
  ```
- **Safe Corrective Action**: Uncheck Dry-Run, confirm token is `k8s-chaos-admin-secure`, and re-click.

---

### 13. Incident Timeline Table is Empty (`No matching incidents`)
- **Symptom**: UI shows `No matching incidents recorded`.
- **Likely Causes**: Filter tab set to `SIMULATION` while only live incidents exist, or database was just cleared.
- **Diagnostic Command**:
  ```powershell
  curl.exe -s http://localhost:5000/api/incidents?limit=5
  ```
- **Safe Corrective Action**: Click the **ALL** filter tab above the table. If empty, click **Toggle HTTP 503** once to create an instant verified incident.

---

### 14. Live Action Stream Does Not Update
- **Symptom**: Action log terminal box in UI remains static.
- **Likely Causes**: Browser tab was backgrounded, throttling `setInterval()`.
- **Diagnostic Command**:
  ```powershell
  curl.exe -s http://localhost:5000/api/logs?limit=5
  ```
- **Safe Corrective Action**: Refresh browser tab (`F5`).

---

### 15. Prometheus Target is Down or Red
- **Symptom**: Prometheus UI (`localhost:9090/targets`) shows `sample-app-service` as `DOWN`.
- **Likely Causes**: Pod crashed or Service port mismatch.
- **Diagnostic Command**:
  ```powershell
  kubectl get endpoints sample-app-service -n health-monitor
  ```
- **Safe Corrective Action**: Verify pods are Ready; if unready, click **Reset All Faults**.

---

### 16. Grafana Dashboard is Unavailable (`localhost:3000` unreachable)
- **Symptom**: Browser cannot connect to Grafana on port 3000.
- **Likely Causes**: Port forward daemon dropped connection.
- **Diagnostic Command**:
  ```powershell
  kubectl get svc -n monitoring | Select-String "grafana"
  ```
- **Safe Corrective Action**:
  ```powershell
  kubectl port-forward svc/prometheus-grafana 3000:80 -n monitoring
  ```

---

### 17. Email Status is Marked `NOT CONFIGURED`
- **Symptom**: Notification Center badge shows `NOT CONFIGURED` in amber.
- **Likely Causes**: Kubernetes Secret `smtp-credentials` missing from `health-monitor` namespace.
- **Diagnostic Command**:
  ```powershell
  kubectl get secret smtp-credentials -n health-monitor
  ```
- **Safe Corrective Action**:
  ```powershell
  .\scripts\setup_smtp_secret.ps1 -UpdateK8sSecret
  kubectl rollout restart deployment/sample-app -n health-monitor
  ```

---

### 18. Email is Marked `SMTP ACCEPTED` but Does Not Arrive in Inbox
- **Symptom**: UI audit shows `SMTP ACCEPTED`, but email not in university inbox.
- **Likely Causes**: Message classified into Spam / Junk folder, or university mail gateway delay.
- **Diagnostic Command**: Check SQLite audit table:
  ```powershell
  curl.exe -s http://localhost:5000/api/notifications/history?limit=1
  ```
- **Verbal Pivot**: *"Notice our delivery audit log: it recorded `status: smtp_accepted` with response code 250 OK from Google's SMTP server `smtp.gmail.com:587`. This proves that our control plane successfully transmitted the message over encrypted STARTTLS. Institutional mail servers at universities frequently quarantine or delay external emails sent via automated relays into the Junk/Spam folder, which is why our system maintains this tamper-evident local audit trail."*

---

### 19. Reset All Faults Does Not Restore State
- **Symptom**: Clicking Reset leaves pod unready.
- **Likely Causes**: Pod is in middle of Kubelet restart backoff.
- **Diagnostic Command**:
  ```powershell
  kubectl get pods -n health-monitor
  ```
- **Safe Corrective Action**: Wait 10 seconds for Kubelet backoff timer, then re-click Reset.
