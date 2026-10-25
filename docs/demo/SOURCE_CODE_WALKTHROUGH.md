# SOURCE CODE WALKTHROUGH & LINE-BY-LINE DEFENSE GUIDE
## Autonomous Kubernetes Health Monitor & Self-Healing Control Plane v2.0
**Institution Technical Evaluation & Source Code Audit Document**

---

### Overview & How to Use This Document
During your final evaluation or viva, the professor may ask you to open a specific file in VS Code or Antigravity and explain its logic line-by-line.

This guide provides an exhaustive, code-grounded walkthrough for all critical functions across the frontend, backend, controller, and Kubernetes manifests.

For every major function, this guide breaks down:
1. **What it does in simple English**
2. **Inputs and outputs**
3. **Execution flow**
4. **How it connects to other components**
5. **What it does NOT do (boundary of responsibility)**
6. **Annotated code excerpt from current repository**
7. **Likely professor question**
8. **Concise answer to say aloud**

---

## Source Code Repository Map

```text
auto-kubernetes-health-monitor/
├── app/
│   ├── controller/
│   │   ├── config.py              # Controller thresholds, intervals, credentials
│   │   ├── Dockerfile             # Multi-stage image build for controller
│   │   └── health_monitor.py      # Core polling loop, pod evaluation & self-healing
│   ├── ui/
│   │   ├── app.py                 # Flask web dashboard, API routes & metrics
│   │   ├── Dockerfile             # Multi-stage image build for web dashboard
│   │   ├── incident_store.py      # SQLite persistent storage & action audit logs
│   │   └── templates/
│   │       └── index.html         # Tailwind CSS UI, live polling & chaos triggers
│   └── notifications.py           # Production SMTP relay (Gmail) & alert formatting
├── k8s/
│   ├── deployment.yaml            # sample-app Deployment, probes, resource limits
│   ├── controller-deployment.yaml # health-monitor-controller Deployment
│   ├── service.yaml               # NodePort Service (30080 -> 5000)
│   ├── hpa.yaml                   # Horizontal Pod Autoscaler (CPU 60%)
│   ├── role.yaml                  # Least-privilege RBAC ClusterRole
│   ├── role-binding.yaml          # ClusterRoleBinding for ServiceAccount
│   ├── service-account.yaml       # health-monitor-sa ServiceAccount
│   └── pvc.yaml                   # incident-data-pvc PersistentVolumeClaim
├── monitoring/
│   ├── alertmanager-config.yaml   # Prometheus Alertmanager routing
│   └── prometheus-rules.yaml      # PodDown, HighCPU, CrashLoop alerts
└── dashboards/
    └── grafana-dashboard.json     # 12-panel Grafana cluster overview dashboard
```

---

## 1. Web Application & API Engine ([app/ui/app.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py))

### 1.1 `_check_destructive_authorization()`
- **Location**: [app/ui/app.py:75-105](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L75-L105)
- **What it does**: Enforces a strict two-layer authorization gate for destructive operations (pod crashes and OOM testing). It prevents accidental disruption from query parameters, GET requests, or unauthorized users.
- **Inputs & Outputs**:
  - *Inputs*: `request.headers` (`X-Chaos-Token` or `Authorization: Bearer <token>`), environment variable `ENABLE_DESTRUCTIVE_CHAOS`.
  - *Outputs*: Tuple `(is_authorized: bool, error_response: Response or None, status_code: int)`.
- **Execution Flow**:
  1. Checks if server policy has `ENABLE_DESTRUCTIVE_CHAOS == True`. If false, rejects with HTTP 403.
  2. Extracts token strictly from HTTP headers (`X-Chaos-Token` or `Authorization`).
  3. Compares token against `CHAOS_AUTH_TOKEN` (default: `k8s-chaos-admin-secure`). If mismatch, returns HTTP 401.
  4. Returns `(True, None, 200)` only when both conditions pass.
- **What it does NOT do**: It does not execute any crash or allocation itself. It only acts as an authentication filter.
- **Annotated Code Excerpt**:
  ```python
  def _check_destructive_authorization():
      # 1. Server-side policy gate
      if not ENABLE_DESTRUCTIVE_CHAOS:
          incident_store.log_action("WARN", "ChaosAPI", "Blocked destructive chaos: ENABLE_DESTRUCTIVE_CHAOS is disabled")
          return False, jsonify({"status": "forbidden", "error": "Destructive operations disabled by policy."}), 403

      # 2. Server-side authorization token gate (strictly from headers)
      auth_header = request.headers.get("X-Chaos-Token") or request.headers.get("Authorization")
      token = None
      if auth_header:
          token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else auth_header.strip()

      if not token or token != CHAOS_AUTH_TOKEN:
          return False, jsonify({"status": "unauthorized", "error": "Valid X-Chaos-Token required."}), 401

      return True, None, 200
  ```
- **Professor Question**: *"Why do you insist on checking the token in HTTP headers instead of accepting a simple query parameter like `?token=xxx`?"*
- **Your Answer**: *"In REST API and DevOps security best practices, query parameters are logged in web server access logs, browser history, proxy logs, and referer headers. Passing secrets in query parameters violates OWASP guidelines. By requiring the custom header `X-Chaos-Token`, we ensure credentials are never leaked in URLs or access logs."*

---

### 1.2 `api_work()` — Bounded CPU Workload
- **Location**: [app/ui/app.py:137-147](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L137-L147)
- **What it does**: Computes squares in a tight loop to burn real CPU cycles inside the container process without spawning external shell processes.
- **Inputs & Outputs**:
  - *Inputs*: Query param `complexity` (integer, default 100, clamped to max 5,000,000).
  - *Outputs*: JSON `{"message": "...", "result": int}`.
- **Execution Flow**:
  1. Parses `complexity` from request arguments.
  2. Enforces safe ceiling: `min(complexity, 5000000)`.
  3. Executes `for i in range(complexity): result += i * i`.
  4. Returns the computed mathematical result.
- **What it does NOT do**: It does not fabricate Prometheus metrics; Prometheus observes the real CPU consumption via cAdvisor.
- **Annotated Code Excerpt**:
  ```python
  @app.route('/api/work')
  def api_work():
      complexity = int(request.args.get('complexity', 100))
      complexity = min(complexity, 5000000)
      
      result = 0
      for i in range(complexity):
          result += i * i
          
      return jsonify({"message": f"Completed work with complexity {complexity}", "result": result})
  ```
- **Professor Question**: *"Why do you clamp complexity to 5,000,000?"*
- **Your Answer**: *"Because in our Kubernetes deployment manifest, each pod has a CPU limit of 200 millicores. If complexity were unbounded (e.g. 100 billion), the container would monopolize the Python GIL and cause Kubelet liveness probes to time out. Clamping ensures measurable CPU spikes while maintaining responsiveness."*

---

### 1.3 `memory_test()` — Controlled Memory Allocation
- **Location**: [app/ui/app.py:149-179](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L149-L179)
- **What it does**: Allocates controlled in-memory string buffers in 20 MB increments up to a safe 50 MB ceiling, and clears them when `size_mb=0`.
- **Inputs & Outputs**:
  - *Inputs*: Query param `size_mb` (integer, e.g. 20 or 0).
  - *Outputs*: JSON with message and total allocated bytes.
- **Execution Flow**:
  1. If `size_mb == 0`: clears `app_state['memory_leak']`, sets Prometheus gauge `MEMORY_ALLOCATED.set(0)`, logs action, and returns.
  2. Calculates current total allocated MB.
  3. Validates boundary: if `current + size_mb > 50`, rejects with HTTP 400.
  4. Appends a string of `1024 * 1024 * size_mb` characters (`'a'`) to `app_state['memory_leak']`.
  5. Updates Prometheus gauge and returns current allocation.
- **What it does NOT do**: It does not allocate native C heap outside Python runtime or trigger an uncoordinated kernel OOM kill.
- **Annotated Code Excerpt**:
  ```python
  @app.route('/api/memory-test')
  def memory_test():
      size_mb = int(request.args.get('size_mb', 10))
      if size_mb == 0:
          app_state["memory_leak"].clear()
          MEMORY_ALLOCATED.set(0)
          return jsonify({"message": "Memory cleared", "memory_allocated_bytes": 0})
      
      total_mb_currently = sum(len(s) for s in app_state["memory_leak"]) // (1024 * 1024)
      if total_mb_currently + size_mb > 50:
          return jsonify({"message": f"Allocation rejected. Current: {total_mb_currently}MB, Target: {total_mb_currently + size_mb}MB, Limit: 50MB"}), 400

      app_state["memory_leak"].append('a' * 1024 * 1024 * size_mb)
      total_bytes = sum(len(s) for s in app_state["memory_leak"])
      MEMORY_ALLOCATED.set(total_bytes)
      return jsonify({"message": f"Allocated {size_mb} MB", "memory_allocated_bytes": total_bytes})
  ```
- **Professor Question**: *"Why do you append strings to a list rather than calling `os.system('stress')`?"*
- **Your Answer**: *"Appending strings in Python ensures memory is allocated directly within the application's process heap and tracked by Python's memory manager. This allows us to observe genuine resident memory increases without installing external binary dependencies inside our minimal Alpine container."*

---

### 1.4 `toggle_readiness()` — Kubernetes Readiness Probe Manipulation
- **Location**: [app/ui/app.py:231-301](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L231-L301)
- **What it does**: Inverts `app_state['is_ready']`, causing `/health/ready` to return HTTP 503 instead of HTTP 200, isolating the pod from Service endpoints without killing the process.
- **Inputs & Outputs**:
  - *Inputs*: HTTP POST request.
  - *Outputs*: JSON with new readiness state, incident ID, and category (`K8S_READINESS`).
- **Execution Flow**:
  1. Toggles boolean: `app_state['is_ready'] = not app_state['is_ready']`.
  2. If now `False`: records an incident in SQLite (`Readiness Failure (HTTP 503)`), sets status `UNREADY`, and dispatches an asynchronous warning notification.
  3. If restored to `True`: updates existing unready incidents in SQLite to `RECOVERED` with `recovery_result='SUCCESS'`, and sends a recovery verified email.
- **What it does NOT do**: It does not restart the container or alter the liveness probe.
- **Annotated Code Excerpt**:
  ```python
  @app.route('/api/chaos/readiness', methods=['POST'])
  def toggle_readiness():
      app_state["is_ready"] = not app_state["is_ready"]
      new_state = app_state["is_ready"]
      pod_name = os.getenv('HOSTNAME', 'Unknown')
      is_in_k8s = os.path.exists('/var/run/secrets/kubernetes.io') or ('sample-app' in pod_name)
      category = "K8S_READINESS" if is_in_k8s else "SIMULATION"

      if not new_state:
          incident = incident_store.record_incident(
              incident_type="Readiness Failure (HTTP 503)",
              target_pod=pod_name,
              detection_source="Readiness Probe (/health/ready)",
              status="UNREADY",
              recovery_action="Kubernetes Service traffic diversion",
              category=category
          )
      else:
          # Mark active readiness incidents as RECOVERED
          # ...
  ```
- **Professor Question**: *"What happens at the Kubernetes networking layer when readiness is toggled to false?"*
- **Your Answer**: *"In `k8s/deployment.yaml`, the readiness probe polls `/health/ready` every 10 seconds with a failure threshold of 2. When it receives HTTP 503 twice, Kubelet signals the Endpoints controller. The Endpoints controller immediately strips the pod's IP address from `sample-app-service`. Kube-proxy updates iptables rules on the node, ensuring no client traffic is routed to this unready pod. The pod remains Running."*

---

### 1.5 `crash()` — Application Exit & Container Restart
- **Location**: [app/ui/app.py:329-378](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L329-L378)
- **What it does**: Authorizes and triggers a deliberate container crash by calling `os._exit(1)` in a background thread, prompting Kubelet to restart the container according to its `restartPolicy`.
- **Inputs & Outputs**:
  - *Inputs*: Headers `X-Chaos-Token`, `X-Dry-Run`.
  - *Outputs*: JSON confirmation with incident ID.
- **Execution Flow**:
  1. Validates authorization via `_check_destructive_authorization()`.
  2. If dry-run: records log and returns HTTP 200 without killing the process.
  3. Records incident `Fatal Application Crash (os._exit)` in SQLite with category `NATIVE_K8S`.
  4. Spawns background thread that sleeps 1 second and executes `os._exit(1)`.
  5. Returns HTTP 200 to caller before process exits.
- **What it does NOT do**: It does not delete the pod object via the Kubernetes API; the Linux process exits, and Kubelet restarts the container inside the existing pod.
- **Annotated Code Excerpt**:
  ```python
  @app.route('/api/chaos/crash', methods=['POST'])
  def crash():
      authorized, err_resp, code = _check_destructive_authorization()
      if not authorized:
          return err_resp, code

      if is_dry_run:
          return jsonify({"status": "success", "mode": "dry_run", "message": "Dry-run verified."}), 200

      if is_in_k8s_container:
          incident_store.record_incident(
              incident_type="Fatal Application Crash (os._exit)",
              target_pod=pod_name,
              detection_source="Kubelet Liveness / Process Watcher",
              status="TRIGGERED",
              category="NATIVE_K8S"
          )
          def kill_pod():
              time.sleep(1)
              os._exit(1)
          threading.Thread(target=kill_pod).start()
          return jsonify({"status": "success", "message": f"Pod {pod_name} terminating in 1s."})
  ```
- **Professor Question**: *"Why did you use `os._exit(1)` instead of `sys.exit(1)` or `raise Exception`?"*
- **Your Answer**: *"In Python, `sys.exit(1)` raises a `SystemExit` exception that can be caught by Flask's WSGI server or try/except blocks, preventing the process from actually terminating. `os._exit(1)` bypasses all Python exception handlers, cleanup hooks, and WSGI layers, issuing an immediate kernel `exit_group()` system call. This accurately replicates a sudden, fatal application segfault or panic."*

---

### 1.6 `cluster_overview()` & `cluster_pods()` — Zero-Fabrication Kubernetes Discovery
- **Location**: [app/ui/app.py:478-657](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L478-L657)
- **What it does**: Communicates directly with the Kubernetes API to discover live pod counts, readiness states, container restart counts, and controller health.
- **Inputs & Outputs**:
  - *Inputs*: In-cluster service account credentials or local `~/.kube/config`.
  - *Outputs*: JSON cluster summary and pod array.
- **Execution Flow**:
  1. Attempts `k8s_config.load_incluster_config()`; falls back to `load_kube_config()`.
  2. If connection fails: sets `k8s_connected = False`, `cluster_status = 'UNAVAILABLE'`, and returns an empty pod list.
  3. If connected: executes `v1.list_namespaced_pod('health-monitor')`.
  4. Inspects each pod's container statuses to count restarts and determine readiness.
  5. Evaluates controller pod status: if running and ready, sets `controller_status = 'RUNNING'`.
- **What it does NOT do**: It never returns fake green pods when the Kubernetes API is down.
- **Annotated Code Excerpt**:
  ```python
  @app.route('/api/cluster/overview', methods=['GET'])
  def cluster_overview():
      try:
          k8s_config.load_incluster_config()
          k8s_connected = True
      except Exception:
          try:
              k8s_config.load_kube_config()
              k8s_connected = True
          except Exception as e:
              k8s_connected = False
              cluster_error = str(e)

      if k8s_connected:
          v1 = client.CoreV1Api()
          pods = v1.list_namespaced_pod("health-monitor", _request_timeout=(2, 2)).items
          for p in pods:
              statuses = p.status.container_statuses or []
              p_ready = bool(statuses and all(cs.ready for cs in statuses))
              if "controller" in p.metadata.name:
                  controller_status = "RUNNING" if (p.status.phase == "Running" and p_ready) else "UNHEALTHY"
  ```
- **Professor Question**: *"Why does the HPA card sometimes show 'HPA resource not queried'?"*
- **Your Answer**: *"In `k8s/role.yaml`, our RBAC ClusterRole grants access to `pods`, `nodes`, `events`, and `deployments`. If the `autoscaling` API group is omitted from the ClusterRole rules, querying `AutoscalingV2Api.read_namespaced_horizontal_pod_autoscaler` raises an HTTP 403 Forbidden exception. Line 543 catches this exception and truthfully reports 'HPA resource not queried' rather than inventing false CPU metrics."*

---

## 2. Health Monitor & Self-Healing Controller ([app/controller/health_monitor.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py))

### 2.1 `evaluate_pod()` — Pod Failure Diagnostics & Self-Protection
- **Location**: [app/controller/health_monitor.py:231-306](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L231-L306)
- **What it does**: Inspects container phases, container waiting reasons, and restart counts to classify a pod as healthy or unhealthy. Applies self-protection to ensure the controller never deletes itself.
- **Inputs & Outputs**:
  - *Inputs*: Kubernetes `V1Pod` object.
  - *Outputs*: Dictionary with keys `healthy`, `reason`, `restarts`, `k8s_recovered_oom`, `is_controller`.
- **Execution Flow**:
  1. Checks pod name and labels. If name contains `controller` or label `app == health-monitor`, flags `is_controller = True`.
  2. Iterates over `pod.status.container_statuses`.
  3. Checks `state.waiting.reason` against `BAD_POD_REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "OOMKilled", "Error", "CreateContainerError"}`.
  4. If container was terminated for `OOMKilled` but is now restarted and running, flags `k8s_recovered_oom = True` and forgives the failure.
  5. If total restarts exceed `config.RESTART_THRESHOLD (5)`, marks pod unhealthy with `ExcessiveRestarts`.
- **Annotated Code Excerpt**:
  ```python
  def evaluate_pod(pod) -> dict:
      pod_name = getattr(pod.metadata, "name", "") or ""
      labels = getattr(pod.metadata, "labels", None) or {}

      # Self-protection: never mark the monitoring controller as unhealthy
      is_controller = (labels.get("app") == "health-monitor" and "controller" in pod_name) or pod_name.startswith("health-monitor-controller")

      phase = getattr(pod.status, "phase", None) or "Unknown"
      # ...
      for cs in container_statuses:
          state = getattr(cs, "state", None)
          if state and getattr(state, "waiting", None):
              waiting_reason = getattr(state.waiting, "reason", "") or ""
              if waiting_reason in BAD_POD_REASONS:
                  reason = waiting_reason

      healthy = (phase == "Running") and (reason not in BAD_POD_REASONS)
      if restarts >= config.RESTART_THRESHOLD:
          healthy = False
          reason = f"ExcessiveRestarts({restarts})"
      return {"healthy": healthy, "reason": reason, "is_controller": is_controller, ...}
  ```
- **Professor Question**: *"Why does line 293 forgive an OOMKilled container if it is currently running?"*
- **Your Answer**: *"Because Kubelet natively restarts containers when they exceed memory limits. If the container restarted, re-initialized, and is now Running and Ready, deleting the pod redundantly would cause unnecessary churn and service interruption. We let native recovery succeed if the pod is stable, and only intervene if it enters `CrashLoopBackOff` or exceeds our restart threshold."*

---

### 2.2 `get_pod_owner_deployment()` — Hierarchy Resolution
- **Location**: [app/controller/health_monitor.py:309-330](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L309-L330)
- **What it does**: Walks Kubernetes `ownerReferences` from a Pod up to its parent ReplicaSet, and then to the parent Deployment.
- **Inputs & Outputs**:
  - *Inputs*: `pod` object, `AppsV1Api` client.
  - *Outputs*: Name of the parent Deployment (string), or `None` if standalone pod.
- **Execution Flow**:
  1. Inspects `pod.metadata.owner_references`.
  2. Finds reference where `kind == "ReplicaSet"`.
  3. Queries `apps_v1.read_namespaced_replica_set(ref.name, namespace)`.
  4. Inspects ReplicaSet's `owner_references` to find `kind == "Deployment"`.
  5. Returns Deployment name (e.g. `"sample-app"`).
- **What it does NOT do**: It does not guess names based on string prefixes. It uses explicit Kubernetes object ownership metadata.
- **Annotated Code Excerpt**:
  ```python
  def get_pod_owner_deployment(pod, apps_v1: client.AppsV1Api) -> str | None:
      namespace = pod.metadata.namespace
      references = pod.metadata.owner_references or []

      for ref in references:
          if ref.kind == "ReplicaSet":
              try:
                  rs = apps_v1.read_namespaced_replica_set(ref.name, namespace)
                  for rs_ref in (rs.metadata.owner_references or []):
                      if rs_ref.kind == "Deployment":
                          return rs_ref.name
              except ApiException:
                  pass
      return None
  ```
- **Professor Question**: *"Why is resolving the parent Deployment necessary before deleting an unhealthy pod?"*
- **Your Answer**: *"In Kubernetes, if you delete a standalone 'naked' pod that is not owned by a ReplicaSet or Deployment, nothing exists to recreate it. The pod is simply gone forever. By verifying the pod is owned by a Deployment, our controller guarantees that deleting the unhealthy pod will cause the ReplicaSet controller to immediately schedule a healthy replacement."*

---

### 2.3 `attempt_recovery()` & `_wait_for_replacement()` — Autonomous Self-Healing & Verification
- **Location**: [app/controller/health_monitor.py:338-582](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L338-L582)
- **What it does**: Manages cooldowns, checks retry thresholds, deletes the bad pod via the Kubernetes API, and actively polls until a replacement pod belonging to the same Deployment becomes `Running` and `Ready`.
- **Inputs & Outputs**:
  - *Inputs*: `pod`, `CoreV1Api`, `AppsV1Api`, `reason`.
  - *Outputs*: None (updates SQLite incident store and dispatches notifications).
- **Execution Flow**:
  1. Checks cooldown: if `time_since_last < config.RECOVERY_COOLDOWN (60s)`, skips attempt.
  2. Checks retries: if `attempts >= config.MAX_RECOVERY_ATTEMPTS (3)`, aborts and sends critical alert.
  3. Resolves parent Deployment via `get_pod_owner_deployment()`.
  4. Deletes pod: `v1.delete_namespaced_pod(pod_name, namespace)`.
  5. Calls `_wait_for_replacement()`: loops up to `RECOVERY_TIMEOUT (120s)` polling pods in namespace.
  6. Filters candidate pods: excludes deleted pod name, excludes terminating pods, and verifies candidate's owner is the target Deployment.
  7. When candidate reaches `phase == 'Running'` and all `container_statuses` have `ready == True`, returns candidate name.
  8. Marks incident `RECOVERED_VERIFIED` in SQLite and dispatches recovery notification.
- **Annotated Code Excerpt**:
  ```python
  def attempt_recovery(pod, v1: client.CoreV1Api, apps_v1: client.AppsV1Api, reason: str):
      # ... Cooldown and retry checks ...
      v1.delete_namespaced_pod(pod_name, namespace)
      logger.info(f"[RECOVERY] Pod {pod_name} deleted. Waiting for replacement...")

      replacement_pod = _wait_for_replacement(deployment_name, namespace, v1, apps_v1, deleted_pod_name=pod_name)
      if replacement_pod:
          send_alert(f"Recovery Successful — Deployment {deployment_name}", event_type="RECOVERY_VERIFIED", ...)
          incident_store.update_incident(inc_id, status="RECOVERED_VERIFIED", category="VERIFIED_REPLACEMENT")
  ```
- **Professor Question**: *"How do you prove that the replacement pod is actually healthy rather than another random pod in the namespace?"*
- **Your Answer**: *"In `_wait_for_replacement()`, lines 558-578 enforce three strict filters: first, line 558 explicitly rejects `deleted_pod_name` so we never match the terminating pod; second, line 561 rejects any pod with a `deletion_timestamp`; third, line 571 verifies the candidate pod is owned by `deployment_name`. Finally, line 576 checks `all(cs.ready for cs in statuses)`. We only declare success when an actual replacement belonging to the workload has passed its readiness probes."*

---

## 3. Real Email & Alert Engine ([app/notifications.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py))

### 3.1 `NotificationConfig.get_masked_config()` — Secret Masking & Status Determination
- **Location**: [app/notifications.py:261-327](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L261-L327)
- **What it does**: Inspects SMTP environment variables and Kubernetes Secrets, sanitizes sensitive credentials for UI and API exposure, and computes the operational delivery state (`LIVE_READY`, `NOT_CONFIGURED`, `DRY_RUN`, `DISABLED`).
- **Inputs & Outputs**:
  - *Inputs*: Environment variables (`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_HOST`, `SMTP_PORT`).
  - *Outputs*: Dictionary with sanitized configuration parameters.
- **Execution Flow**:
  1. Reads `SMTP_USERNAME`. If email format, masks local part (e.g. `sainaath.r738507` becomes `sa***@gmail.com`).
  2. If password is set, masks as `••••••••`; never returns raw text.
  3. Checks if credentials exist: if missing, sets state `NOT_CONFIGURED`.
  4. If credentials exist and `is_dry_run()` is false, sets state `LIVE_READY`.
  5. Determines encryption mode: `STARTTLS` (port 587) vs `SSL/TLS` (port 465).
- **Annotated Code Excerpt**:
  ```python
  @classmethod
  def get_masked_config(cls) -> Dict[str, Any]:
      user = cls.get_smtp_username()
      if user and "@" in user:
          parts = user.split("@")
          masked_user = f"{parts[0][:2]}***@{parts[1]}"
      elif user:
          masked_user = f"{user[:2]}***"
      else:
          masked_user = "Not Configured"

      passwd = cls.get_smtp_password()
      masked_pass = "••••••••" if passwd else "Not Configured"
      # ...
      return {
          "delivery_state": delivery_state,
          "smtp_username": masked_user,
          "smtp_host": cls.get_smtp_host(),
          "smtp_port": cls.get_smtp_port(),
          "encryption_mode": encryption_mode,
          ...
      }
  ```
- **Professor Question**: *"Why did earlier screenshots show `NOT CONFIGURED` and `Last Successful SMTP: None yet`?"*
- **Your Answer**: *"In earlier development phases before the Kubernetes Secret `smtp-credentials` was mounted, `get_smtp_username()` returned empty. Line 287 accurately detected that required sending credentials were missing, categorizing delivery as `NOT_CONFIGURED`. Once the Secret was provisioned with valid credentials, `get_masked_config()` dynamically transitioned to `LIVE_READY`, and our first live test recorded a real delivery timestamp."*

---

### 3.2 `_send_smtp_email()` & `send_notification()` — Non-Blocking Delivery & Deduplication
- **Location**: [app/notifications.py:334-343](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L334-L343), [app/notifications.py:460-600](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L460-L600)
- **What it does**: Deduplicates repeated alerts using a cooldown window, formats multipart MIME messages (plain text + HTML), and executes SMTP network transport with STARTTLS and retry backoff.
- **Inputs & Outputs**:
  - *Inputs*: `event_type`, `incident_id`, `severity`, `target_workload`, `target_pod`, `problem`.
  - *Outputs*: Dictionary with delivery `status` (`smtp_accepted`, `sent`, `failed`, `suppressed`).
- **Execution Flow**:
  1. Computes deduplication key: `f"{target_workload}:{event_type}"`.
  2. If an identical alert was dispatched within `ALERT_COOLDOWN_SEC (300s)`, suppresses delivery to prevent email spam.
  3. Connects to `smtp.gmail.com:587` with a 10-second socket timeout.
  4. Calls `server.starttls()`.
  5. Authenticates using `server.login(user, password)`.
  6. Sends mail and receives SMTP 250 response.
  7. Persists record in SQLite `notifications` table.
- **Annotated Code Excerpt**:
  ```python
  def _send_smtp_email(to_email, subject, text_body, html_body, incident_id):
      # Timeout and retry loop
      for attempt in range(1, max_retries + 1):
          try:
              server = smtplib.SMTP(host, port, timeout=timeout)
              if use_tls:
                  server.starttls()
              server.login(username, password)
              server.send_message(msg)
              server.quit()
              return {"status": "smtp_accepted", "attempt": attempt, ...}
          except Exception as exc:
              # Log sanitized error without exposing credentials
  ```
- **Professor Question**: *"What happens if the university network blocks port 587 or the internet disconnects?"*
- **Your Answer**: *"In [app/controller/health_monitor.py:182](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L182), `send_alert` launches the notification in a daemon thread: `threading.Thread(target=_do_send, daemon=True).start()`. The main controller loop never blocks on SMTP network sockets. If the connection times out, line 170 logs `[ALERT-TIMEOUT]`, records `timed_out` in SQLite, and the controller continues monitoring Kubernetes uninterrupted."*

---

## 4. SQLite Incident Store ([app/ui/incident_store.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/incident_store.py))

### 4.1 Schema Definition & Context Manager
- **Location**: [app/ui/incident_store.py:25-80](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/incident_store.py#L25-L80)
- **What it does**: Provides concurrency-safe SQLite connection management and creates tables for `incidents`, `action_logs`, and `notifications`.
- **Inputs & Outputs**:
  - *Inputs*: Database path from `INCIDENT_DB_PATH` (default: `/app/data/incidents.db`).
  - *Outputs*: Active connection yield with automatic commit and close.
- **Key Schema**:
  ```sql
  CREATE TABLE incidents (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      incident_id TEXT UNIQUE NOT NULL,
      incident_type TEXT NOT NULL,
      target_workload TEXT NOT NULL,
      target_pod TEXT NOT NULL,
      detection_source TEXT NOT NULL,
      detection_time TEXT NOT NULL,
      status TEXT NOT NULL,
      recovery_action TEXT,
      recovery_result TEXT,
      evidence TEXT,
      category TEXT DEFAULT 'GENERAL',
      is_simulated INTEGER DEFAULT 0
  );
  ```
- **Professor Question**: *"Why SQLite on a PVC instead of an external MySQL or PostgreSQL database?"*
- **Your Answer**: *"For edge Kubernetes clusters and self-contained control planes, running an embedded SQLite database on a PersistentVolumeClaim provides zero-dependency persistence without running a heavy database pod. It handles our telemetry throughput (~50-100 writes per hour) with microsecond latency, zero network hops, and survives pod restarts."*

---

## 5. Kubernetes Infrastructure Manifests

### 5.1 `k8s/deployment.yaml` — Probes & Resource Constraints
- **Key Section**: Lines 78–103
- **Annotated Excerpt**:
  ```yaml
  resources:
    requests:
      cpu: "50m"      # 5% of 1 CPU core
      memory: "64Mi"  # 64 Mebibytes
    limits:
      cpu: "200m"     # 20% of 1 CPU core
      memory: "128Mi" # Hard ceiling (triggers OOM if exceeded)

  livenessProbe:
    httpGet:
      path: /health/live
      port: 5000
    initialDelaySeconds: 10
    periodSeconds: 15
    failureThreshold: 3

  readinessProbe:
    httpGet:
      path: /health/ready
      port: 5000
    initialDelaySeconds: 5
    periodSeconds: 10
    failureThreshold: 2
  ```
- **Professor Question**: *"What is the difference between CPU requests/limits and Memory requests/limits?"*
- **Your Answer**: *"CPU is a compressible resource: if a pod exceeds its 200m limit, Linux CFS throttles its CPU time slices, causing slowdowns but not termination. Memory is an incompressible resource: if a pod exceeds its 128Mi limit, the Linux kernel cannot compress memory, so the cgroup OOM killer sends `SIGKILL` (ExitCode 137), terminating the container."*

---

### 5.2 `k8s/hpa.yaml` — Horizontal Pod Autoscaling Policy
- **Key Section**: Lines 10–31
- **Annotated Excerpt**:
  ```yaml
  apiVersion: autoscaling/v2
  kind: HorizontalPodAutoscaler
  metadata:
    name: sample-app-hpa
    namespace: health-monitor
  spec:
    scaleTargetRef:
      apiVersion: apps/v1
      kind: Deployment
      name: sample-app
    minReplicas: 2
    maxReplicas: 6
    metrics:
      - type: Resource
        resource:
          name: cpu
          target:
            type: Utilization
            averageUtilization: 60
  ```
- **Professor Question**: *"How does the HPA calculate 60% CPU utilization?"*
- **Your Answer**: *"The 60% target is calculated relative to the pod's CPU **request**, not its limit. In `deployment.yaml`, the CPU request is 50m. Therefore, 60% average utilization means $0.60 \times 50\text{m} = 30\text{m}$. If the average CPU across all running pods exceeds 30 millicores over the Metrics Server scrape window, HPA calculates `desiredReplicas = ceil[currentReplicas * (currentUtilization / targetUtilization)]` and scales out the Deployment."*

---

### 5.3 `k8s/role.yaml` — RBAC Least-Privilege Security
- **Key Section**: Lines 13–39
- **Annotated Excerpt**:
  ```yaml
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRole
  metadata:
    name: health-monitor-role
  rules:
    - apiGroups: [""]
      resources: ["pods"]
      verbs: ["get", "list", "watch", "delete"]
    - apiGroups: [""]
      resources: ["nodes"]
      verbs: ["get", "list", "watch"]
    - apiGroups: [""]
      resources: ["events"]
      verbs: ["get", "list", "watch"]
    - apiGroups: ["apps"]
      resources: ["deployments", "replicasets"]
      verbs: ["get", "list", "watch"]
  ```
- **Professor Question**: *"Why did you use ClusterRole instead of Role, and is it secure?"*
- **Your Answer**: *"We used a ClusterRole because our controller evaluates node health (`check_nodes()` in [health_monitor.py:587](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L587)) to check for `NodeMemoryPressure` and `NodeNotReady`, which are cluster-scoped resources outside namespaces. It adheres strictly to least-privilege: the controller is only permitted to `delete` pods; it has read-only access to nodes, deployments, and events, and has zero permissions to read Secrets or modify cluster configuration."*
