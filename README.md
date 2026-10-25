# ⎈ Auto Kubernetes Health Monitoring & Self-Healing System

> **DevOps & Cloud Infrastructure Platform** | Kubernetes (Minikube) · Custom Python Controller · Flask Observability UI · SQLite Persistence · Prometheus · Grafana · HPA

---

## 📌 1. Project Overview & Architecture

Modern cloud microservices face transient failures, memory leaks, unready dependencies, and traffic spikes. While standard Kubernetes controllers manage basic container crashes, production reliability requires intelligent, contextual remediation, persistent incident auditing, and live cluster visibility.

This project delivers an end-to-end autonomous health monitoring, self-healing, and autoscaling control plane featuring:

```
                                  ┌───────────────────────────┐
                                  │   Prometheus & Grafana    │
                                  │   (Metrics & Visuals)     │
                                  └─────────────▲─────────────┘
                                                │ Scrapes /metrics
┌─────────────────────────────┐   Polls State   │
│  Custom Health Controller   │◄────────────────┼────────────────┐
│  • Self-Protection          │                 │                │
│  • Deployment Verification  │                 ▼                │
│  • Native Recovery Tracking │      ┌─────────────────────┐     │
└──────────────┬──────────────┘      │   sample-app (x2)   │     │
               │ Triggers Remediation│   (Flask App Pods)  │     │
               ▼                     └──────────┬──────────┘     │
┌─────────────────────────────┐                 │ Writes Records │
│     Kubernetes API Server   │                 ▼                │
│   (Kubelet, Deployments)    │      ┌─────────────────────┐     │
└─────────────────────────────┘      │ SQLite Incident DB  │─────┘
                                     │ (Persistent Volume) │
                                     └─────────────────────┘
```

### Core Components

1. **Custom Health Controller (`app/controller/health_monitor.py`)**:
   - Runs in-cluster with least-privilege RBAC (`health-monitor-sa`).
   - **Self-Protection**: Explicitly identifies and protects itself (`health-monitor-controller`), preventing accidental deletion.
   - **Deployment-Specific Verification**: When healing an unhealthy pod, verifies the parent Deployment (`sample-app`), deletes the bad pod, and confirms that a *new* replacement pod belonging to that exact Deployment reaches `Running` and `Ready == True` (never marks recovery successful merely because some unrelated pod is Running).
   - **Native Recovery Awareness**: Distinguishes Kubernetes-native container restarts (e.g. Kubelet restarting a container after OOM or non-zero exit) from continuous CrashLoopBackOffs, holding back redundant pod deletions when the pod is already healthy.
   - **Audit Telemetry**: Records structured incidents directly into persistent SQLite storage.

2. **Web Control Plane & DevOps Dashboard (`app/ui/`)**:
   - Dark-themed DevOps interface (`templates/index.html`).
   - **Live Cluster Status**: Displays live pod phases, readiness, container restarts, and node assignments.
   - **Explicit Unavailable State**: If Kubernetes is offline or unreachable, cluster overview and pod endpoints return an explicit `UNAVAILABLE` state with 0 pods and clear error messaging (never reports a fabricated Running pod or fake healthy cluster).
   - **Simulation Controls**: Safe bounded CPU workloads (`/api/work`), readiness toggles (`/api/chaos/readiness`), and bounded memory allocations (`/api/memory-test`, strictly capped at 50MB).
   - **Strictly Gated Chaos**: Destructive crash and OOM endpoints are disabled by default on the server and require genuine server-side authorization tokens (`X-Chaos-Token`), with dry-run support.

3. **Persistent Incident Tracking (`app/ui/incident_store.py`)**:
   - Backed by SQLite (`incidents.db`), supporting both local runs and Kubernetes PersistentVolumeClaims (`incident-data-pvc`).
   - Accurately classifies and distinguishes 5 distinct failure & recovery categories:
     1. **`SIMULATION`**: Simulated local faults and automated simulation recovery timers.
     2. **`K8S_READINESS`**: Real Kubernetes readiness probe failures (/health/ready HTTP 503) and Service traffic isolation.
     3. **`NATIVE_K8S`**: Native Kubelet container restarts (OOMKilled / process exits) verified Running and Ready.
     4. **`CONTROLLER_ACTION`**: Remediation actions executed by the custom controller (pod deletion).
     5. **`VERIFIED_REPLACEMENT`**: Verified new replacement pod for the target Deployment becoming Running & Ready.

4. **Metrics & Autoscaling (HPA)**:
   - Prometheus metrics exposed via `/metrics` (`http_requests_total`, `http_request_duration_seconds`, `http_requests_in_progress`, `app_memory_allocated_bytes`).
   - ServiceMonitor (`k8s/service-monitor.yaml`) and Prometheus annotations on Service (`k8s/service.yaml`).
   - Horizontal Pod Autoscaler (`k8s/hpa.yaml`) scaling between 2 and 6 replicas at 60% CPU utilization.
   - Grafana dashboard (`dashboards/grafana-dashboard.json`) provisioned with HTTP rate, latency, memory, and error panels.

---

## 🔒 2. Safety Controls & Server-Side Gates

Destructive operations (`/api/chaos/oom` and `/api/chaos/crash`) are strictly secured:

1. **Disabled by Default**:
   - The server requires the environment variable `ENABLE_DESTRUCTIVE_CHAOS="true"`. By default, this is `"false"`.
   - Any request received when disabled returns `HTTP 403 Forbidden`.
2. **Server-Side Authorization**:
   - When enabled, callers must provide a genuine server-side authorization token via the `X-Chaos-Token` header or `Authorization: Bearer <token>`.
   - The token is validated against `CHAOS_AUTH_TOKEN` (default: `k8s-chaos-admin-secure`).
   - **No Checkbox/Query Bypass**: Passing `?allow_oom=true` or `?confirm=true` alone will NEVER authorize destructive operations.
3. **Dry-Run Mode for Verification**:
   - Pass header `X-Dry-Run: true` or `?dry_run=true` to test and validate the safety gate without terminating processes or allocating memory.
   - Ordinary unit and automated tests verify authorization gates using dry-run mode, ensuring 200MB memory allocations never occur during test runs.

---

## 🔌 3. Port & Resource Alignment

The project uses a unified and consistent port architecture across all layers:

| Layer | Configuration | Port | Target / Notes |
|---|---|---|---|
| **Flask App** | `app/ui/app.py` | `5000` | Configured via `PORT` env var (default: 5000) |
| **Dockerfile** | `app/ui/Dockerfile` | `5000` | `ENV PORT=5000`, `EXPOSE 5000` |
| **Deployment** | `k8s/deployment.yaml` | `5000` | `containerPort: 5000`, `PORT: "5000"` |
| **Probes** | `livenessProbe` & `readinessProbe` | `5000` | Checks `/health/live` and `/health/ready` on 5000 |
| **Service** | `k8s/service.yaml` | `80` | `port: 80` (cluster-internal), `targetPort: 5000` |
| **NodePort** | `k8s/service.yaml` | `30080` | Laptop access port on Minikube node |
| **ServiceMonitor** | `k8s/service-monitor.yaml` | `http` | Scrapes port named `http` (Service port 80 -> 5000) |

---

## 💾 4. Persistent Storage Architecture

### Local Execution:
- Incident and action log data is stored in `incidents.db` in the application directory (or configured via `INCIDENT_DB_PATH`).
- Data persists across browser refreshes, UI reloads, and application restarts.

### Kubernetes Deployment:
- Manifest `k8s/pvc.yaml` defines two standard PersistentVolumeClaims:
  1. **`incident-data-pvc`** (128Mi): Mounted to the sample app at `/app/data` with `INCIDENT_DB_PATH=/app/data/incidents.db`. Incidents survive container restarts and pod recreation.
  2. **`controller-logs-pvc`** (128Mi): Mounted to the controller at `/app/logs` for persistent controller activity logs.
- *Known Limitation*: If deployed without a PVC or on an ephemeral `emptyDir`, incident data remains persistent during in-container execution and page reloads, but will reset if the Kubernetes pod is deleted and replaced by ReplicaSet.

---

## 📬 5. Alerting & Notification System (SMTP & Telegram)

The control plane includes a production-grade, non-blocking notification engine supporting both Email (SMTP) and optional Telegram alerts:

### Key Capabilities:
1. **Configurable Alert Triggers**:
   - `CRITICAL_FAILURE`: Node unreadiness, node pressure, unhandled CrashLoopBackOffs, OOMKilled events.
   - `UNHEALTHY_PROBE`: Repeated HTTP 503 readiness probe failures.
   - `RECOVERY_STARTED`: Controller initiated pod deletion or restart.
   - `RECOVERY_VERIFIED`: Confirmed new replacement pod reached Running & Ready (alerts sent **only after** recovery verification).
   - `RECOVERY_FAILED`: Max recovery attempts exceeded or timeout.
   - `TEST`: Safe diagnostic verification alerts.
2. **Default Recipient**:
   - Out-of-the-box alerts are directed to: **`sainaath.r2025@vitstudent.ac.in`**.
   - Fully configurable via the `ALERT_TO_EMAIL` environment variable.
3. **Non-Blocking Fault Isolation**:
   - SMTP connection timeouts, network partitions, authentication errors, or invalid recipients never crash the controller or block Kubernetes self-healing.
4. **Deduplication & Cooldown**:
   - Identical alerts for the same pod and failure mode are suppressed within a configurable window (default: 300s) to prevent notification storms.
5. **Clear Operational Status Codes**:
   - `sent`: Successfully delivered and confirmed by the remote SMTP server.
   - `dry_run`: Synthetically formatted, validated, and logged to audit store without opening network sockets.
   - `missing_config`: Real delivery requested while `SMTP_USER` or `SMTP_PASSWORD` is absent.
   - `failed`: Trapped SMTP or network exception (auth error, timeout, rejected recipient) safely recorded with details.
   - `disabled`: Alert backend explicitly set to `none`.
   - `suppressed`: Duplicate event within cooldown window.
6. **Secret Masking**:
   - Passwords and tokens are strictly masked in logs and `/api/notifications/status` API responses (`••••••••`).

### SMTP & Alert Configuration Reference:
Configuration can be supplied via `.env` (see `.env.example`) or Kubernetes Secrets:

| Variable | Default | Description |
|---|---|---|
| `ALERT_BACKEND` | `email` | Notification channel: `email`, `telegram`, `both`, or `none` |
| `ALERT_ENABLED` | `true` | Master toggle for alerts (`true` / `false`) |
| `ALERT_TO_EMAIL` | `sainaath.r2025@vitstudent.ac.in` | Intended recipient email address |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname (configurable for any provider) |
| `SMTP_PORT` | `587` | Port: `587` (STARTTLS) or `465` (direct SSL/TLS) |
| `SMTP_USER` | `""` | SMTP username / address (required for live delivery) |
| `SMTP_PASSWORD` | `""` | SMTP password or App Password (never hardcoded in Git) |
| `SMTP_FROM` | `k8s-monitor@cloud-health.local` | Sender envelope address |
| `SMTP_USE_TLS` | `true` | Enable STARTTLS (default `true` for port 587) |
| `SMTP_USE_SSL` | `false` | Enable direct SSL socket (default `true` for port 465) |
| `SMTP_TIMEOUT_SEC` | `10` | Socket connection & delivery timeout in seconds |
| `SMTP_DRY_RUN` | `true` (auto) | Set `false` for live dispatch with genuine SMTP credentials |
| `ALERT_COOLDOWN_SEC`| `300` | Minimum seconds between identical alerts |
| `TELEGRAM_BOT_TOKEN`| `""` | Optional Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | `""` | Optional Telegram chat or channel ID |

### Testing Notifications:

#### Option A — Via Command-Line Interface (CLI):
```powershell
# 1. Inspect configuration status & validation (secrets safely masked)
py -m app.notifications --check-config

# 2. Run a safe dry-run test (validates formatting & templates without credentials)
py -m app.notifications --test

# 3. Test genuine live SMTP delivery (requires valid SMTP_USER & SMTP_PASSWORD)
py -m app.notifications --live

# 4. Test delivery to a custom recipient
py -m app.notifications --test --recipient sainaath.r2025@vitstudent.ac.in
```

#### Option B — Via Web Dashboard:
1. Navigate to the **Notifications Center** card at the bottom of the dashboard (`http://localhost:5000`).
2. Click **⚡ Safe Dry-Run** to verify template formatting, incident recording, and audit logs.
3. Click **📨 Live SMTP Test** to attempt live delivery using credentials in `.env`.
4. Check the **Recent Notification Deliveries** audit table for immediate delivery confirmation or error reporting.

### SMTP Troubleshooting Guide:

| Symptom / Error | Root Cause | Resolution |
|---|---|---|
| `status: missing_config` | `SMTP_USER` or `SMTP_PASSWORD` is blank in `.env`. | Configure valid credentials in `.env` or set `SMTP_DRY_RUN=true`. |
| `SMTPAuthenticationError (535)` | Invalid password or 2FA required. | For Gmail, generate a 16-character **App Password** from Google Account Security. |
| `Connection timed out` | Firewall or ISP blocking SMTP port. | Test port 587 (STARTTLS) vs port 465 (SSL). Ensure outbound TCP traffic is permitted. |
| `SMTPRecipientsRefused (550)` | Destination mailbox unrecognized or blocked. | Verify `ALERT_TO_EMAIL` address syntax and mailbox existence. |

---

## 🚀 6. Quick Start & Interactive Demo

An interactive PowerShell demonstration tool is included for Windows PowerShell:

```powershell
.\scripts\demo_health_monitor.ps1
```

### Interactive Menu Options:
- **[1] Run All Unit & Safety Tests**: Executes all 40 automated test cases.
- **[2] Start Web Dashboard locally**: Boots Flask UI on `http://localhost:5000` with the live control plane.
- **[3] Check Live Kubernetes / Minikube Status**: Inspects nodes, pods, and HPA without disruption.
- **[4] Run Traffic Generator**: Sends concurrent load to trigger CPU metrics and test autoscaling.
- **[5] View Persistent Incident History**: Queries the SQLite `incidents.db` to audit recorded events.
- **[6] Update Grafana Dashboard Configuration**: Validates PromQL panels and pushes to Grafana.
- **[7] Check Alerting / SMTP Configuration & Send Safe Test Alert**: Inspects masked config and dispatches safe test alert.
- **[8] Run Controlled Readiness Failure & Recovery Demonstration**: Full automated end-to-end failure, isolation, and recovery loop.
- **[9] Exit**

---

## 🛠️ 6. Full Deployment to Minikube

### Prerequisites:
- Windows 11 with PowerShell & Docker Desktop running.
- Minikube installed (`minikube version`).
- `kubectl` configured.

### Step-by-Step Cluster Setup:
```powershell
# 1. Start Minikube and enable metrics-server
minikube start --driver=docker --cpus=2 --memory=4096
minikube addons enable metrics-server

# 2. Point local terminal to Minikube's Docker daemon
& minikube -p minikube docker-env --shell powershell | Invoke-Expression

# 3. Build container images inside Minikube
docker build -t sample-app-ui:latest ./app/ui
docker build -t health-monitor:latest ./app/controller

# 4. Deploy Persistent Volumes, Namespace, and RBAC
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/service-account.yaml
kubectl apply -f k8s/role.yaml
kubectl apply -f k8s/role-binding.yaml
kubectl apply -f k8s/pvc.yaml

# 5. Deploy Application, Service, HPA, and ServiceMonitor
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/service-monitor.yaml

# 6. Deploy Custom Controller
kubectl apply -f k8s/controller-deployment.yaml

# 7. Access Web Dashboard
minikube service sample-app-service -n health-monitor
```

---

## 📈 7. Monitoring & Autoscaling Verification

### 1. Generating Load Against Kubernetes Service:
```powershell
# Run traffic generator against the Kubernetes Service
py scripts/generate_traffic.py --url http://127.0.0.1:30080 --workers 8 --duration 45 --complexity 500000
```

### 2. Observing HPA Scaling:
Open a PowerShell terminal and monitor the HPA replica transitions:
```powershell
kubectl get hpa sample-app-hpa -n health-monitor -w
```
- **Before Traffic**: Replicas: `2`, CPU: `1-5% / 60%`.
- **During Traffic**: Replicas scale up to `3-5`, CPU rises above target (`70-95% / 60%`).
- **After Traffic**: After cooldown period (default 5m), HPA safely scales replicas back down to `2`.

---

---

## 🎨 8. Sample App UI Alignment & Responsive Design Improvements

The DevOps web control plane (`http://localhost:5000`) has been carefully aligned and polished for both high-resolution desktop monitors and laptop screens:

1. **Balanced Simulation vs. Chaos Grid**:
   - Equalized card heights and matching baselines for the **Simulation Controls (Safe)** and **Destructive Fault Injection** panels.
   - Pod Crash Injection and OOMKilled controls sit cleanly side-by-side with aligned tags and action buttons.
   - Observability links (Grafana & Prometheus) are embedded in a dedicated sub-panel with clear external link indicators.
2. **Incident Timeline & Live Stream Alignment**:
   - Fixed table column width clipping (`min-w-[140px]`, `whitespace-nowrap`) preventing long incident IDs (`INC-20261016-xxxxxx`) and pod names from wrapping across lines.
   - Equalized scroll viewport heights (`max-h-[320px]`) for the Incident Timeline and Live Action Stream terminal.
3. **Enhanced Notifications Center**:
   - Dedicated card displaying operational SMTP parameters: backend channel, SMTP server, sender envelope, target recipient, encryption mode, and cooldown window.
   - Target recipient `sainaath.r2025@vitstudent.ac.in` displayed clearly without truncation or clipping.
   - Dual test triggers: **⚡ Safe Dry-Run** (instant synthetic check) and **📨 Live SMTP Test** (genuine delivery attempt).
   - Dynamic status badges: `CONFIRMED`, `DRY_RUN`, `MISSING_CONFIG`, and `FAILED` with actionable error tooltips.
4. **Responsive Flexbox/Grid Layout**:
   - Fluid responsiveness across 1920x1080 desktop and 1366x768 laptop resolutions without page horizontal scrollbars.
   - Preserves all dark DevOps design aesthetics, high-contrast badges, and interactive controls.

---

## 🧪 9. Automated Unit & Safety Test Suite

The test suite covers **47 comprehensive automated test cases**, all passing with zero failures:

```powershell
py -m pytest tests/ -v
# or
py -m unittest discover -s tests -p "test_*.py" -v
```

### Test Suite Breakdown:
- **`tests/test_notifications.py` (16 tests)**:
  - Default recipient verification (`sainaath.r2025@vitstudent.ac.in` when unset).
  - Missing SMTP configuration fallback to safe dry-run mode.
  - Live delivery request without credentials reports explicit `missing_config`.
  - Port validation detecting missing host, non-integer port values, and out-of-range ports.
  - Disabled alert backend handling without error.
  - Dry-run validation and incident store audit persistence.
  - Successful mocked SMTP delivery with STARTTLS (port 587) and authentication.
  - Successful mocked direct SSL/TLS delivery (port 465) via `SMTP_SSL`.
  - Trapped SMTP authentication errors (`SMTPAuthenticationError`) without crashing.
  - Trapped recipient mailbox refusal (`SMTPRecipientsRefused`) without crashing.
  - Non-blocking SMTP connection failure handling (network partitions never crash controller).
  - Incident-based deduplication and cooldown suppression (prevents notification storms).
  - Structured incident details in Subject, Text, and HTML bodies.
  - Secret masking ensuring credentials are never exposed in logs or APIs.
  - Safe diagnostic test notification dispatcher.
  - CLI execution tests (`--check-config`, `--test`, `--recipient`).
- **`tests/test_ui.py` (11 tests)**:
  - Destructive OOM safety gate verification without 200MB memory exhaustion.
  - Query parameter bypass rejection.
  - Authorized dry-run validation.
  - Readiness toggle (HTTP 503) and recovery.
  - Explicit unavailable state verification when Kubernetes is disconnected (zero fabricated pods).
  - Live Kubernetes mocked overview and pod parsing.
  - Bounded memory limits (capped at 50MB) and reset endpoint.
  - Prometheus metrics instrumentation (`http_requests_total`, latency histogram).
  - Index dashboard rendering and section validation.
- **`tests/test_controller.py` (11 tests)**:
  - Controller self-protection logic (protected from self-deletion).
  - Deployment-specific replacement verification (rejects pods from other deployments).
  - Native K8s recovery detection (skips pods healed natively by Kubelet).
  - Cooldown timer enforcement and max recovery attempts abort.
  - Verified replacement pod name assertions.
- **`tests/test_crash_recovery.py` (5 tests)**:
  - Crash endpoint disabled by default (403 Forbidden).
  - Query parameter `?confirm=true` bypass rejection.
  - Invalid token rejection (401 Unauthorized).
  - Authorized dry-run safety verification.
  - Simulated crash incident recording with `SIMULATION` category.
- **`tests/test_incident_store.py` (4 tests)**:
  - SQLite database initialization and schema migration.
  - Category persistence (`SIMULATION`, `K8S_READINESS`, `NATIVE_K8S`, `CONTROLLER_ACTION`, `VERIFIED_REPLACEMENT`).
  - Category filtering and chronological ordering.
  - Clean connection context management (no unclosed database resource warnings).

---

## ⚠️ 9. Troubleshooting & Known Limitations

1. **Single-Node Cluster Limitation**:
   - In a single-node Minikube cluster, if the node enters `NotReady` or reports pressure, the controller alerts on the failure, but automated node failover cannot reschedule pods to another node because no secondary worker nodes exist.
2. **Minikube NodePort Reachability on Windows**:
   - On Windows with Docker Desktop driver, NodePort `30080` may require `minikube service sample-app-service -n health-monitor --url` or `minikube tunnel` to route traffic into the cluster.
3. **Metrics-Server Readiness**:
   - `metrics-server` takes approximately 30–60 seconds after cluster boot to establish its first metrics scrape. During this window, HPA reports `<unknown>/60%` before displaying numerical CPU percentages.
4. **Preserving Existing Monitoring Deployments**:
   - Do not delete existing Prometheus or Grafana deployments when deploying the sample app. All Prometheus rules and ServiceMonitors are namespaced and configured non-destructively.
