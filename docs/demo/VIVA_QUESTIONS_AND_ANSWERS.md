# TECHNICAL VIVA DEFENSE GUIDE & 45 PROFESSOR Q&As
## Autonomous Kubernetes Health Monitor & Self-Healing Control Plane v2.0
**Comprehensive Examination & Technical Defense Document**

---

### How to Use This Defense Guide
During your oral viva examination, professors will ask probing questions to test whether you wrote the code yourself, whether you understand low-level Kubernetes systems, and whether you understand the architectural limitations of your implementation.

Every question below provides:
1. **Short Verbal Answer**: A crisp, authoritative 2-to-3 sentence answer you can speak aloud immediately.
2. **Deep Technical Explanation**: The underlying kernel, network, or distributed systems theory.
3. **Source Code / CLI Reference**: Exact file, line numbers, or commands that prove your answer.

---

## Part 1: Kubernetes Core Architecture & Workload Primitives

#### Q1: What is the fundamental difference between a Pod, a ReplicaSet, and a Deployment?
- **Short Verbal Answer**: *"A Pod is the smallest deployable execution unit containing one or more containers sharing a network namespace and volumes. A ReplicaSet ensures that a specified number of identical pod replicas are running at all times using label selectors. A Deployment is a higher-level abstraction that manages ReplicaSets, enabling declarative rolling updates, rollbacks, and replica scaling."*
- **Deep Technical Explanation**: The Deployment controller does not manage pods directly; it manages ReplicaSets. When you update a Deployment's container image, it creates a new ReplicaSet and scales it up while scaling the old ReplicaSet down. This is why our self-healing controller walks the ownership chain: Pod $\rightarrow$ ReplicaSet $\rightarrow$ Deployment.
- **Source Reference**: [app/controller/health_monitor.py:309-330](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L309-L330) (`get_pod_owner_deployment()`).

#### Q2: How does a Kubernetes Service route traffic to pods, and how are labels and selectors used?
- **Short Verbal Answer**: *"A Service provides a stable virtual IP (ClusterIP) and DNS name. It uses a label selector (e.g. `app: sample-app`) to query the Kubernetes API for matching pods. The Endpoints controller populates an Endpoints object with the private IP addresses of all healthy, Ready pods, and kube-proxy configures iptables or IPVS rules on each node to load balance traffic across those IPs."*
- **Deep Technical Explanation**: Pods are ephemeral and their IP addresses change whenever they are restarted or rescheduled. A Service decouples frontend callers from backend pod lifecycles.
- **Source Reference**: [k8s/service.yaml:23-24](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/service.yaml#L23-L24) (`selector: app: sample-app`).

#### Q3: What is the exact difference between a Liveness Probe and a Readiness Probe?
- **Short Verbal Answer**: *"A Liveness Probe determines if the container process is still running and healthy; if it fails, Kubelet terminates and restarts the container. A Readiness Probe determines if the container is ready to accept incoming user traffic; if it fails, Kubelet removes the pod's IP address from Service Endpoints without restarting the container."*
- **Deep Technical Explanation**: Liveness protects against fatal application deadlocks and infinite loops. Readiness protects against temporary startup latencies, warm-up caches, or backend database disconnections.
- **Source Reference**: [k8s/deployment.yaml:87-102](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml#L87-L102), [app/ui/app.py:114-125](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L114-L125).

#### Q4: Why does toggling readiness to HTTP 503 isolate the pod but not restart it?
- **Short Verbal Answer**: *"Because Kubelet treats readiness and liveness as orthogonal lifecycles. The readiness probe only signals the Endpoints controller to detach the pod from iptables routing. Because the process is still answering HTTP requests on `/health/live` with HTTP 200, Kubelet knows the process is alive, so no restart is triggered."*
- **Deep Technical Explanation**: In microservice architectures, a service might be overloaded and need a temporary cooldown. Restarting it would destroy cached state and exacerbate cascading failures.
- **Source Reference**: [app/ui/app.py:231-255](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L231-L255), `kubectl get endpoints sample-app-service`.

#### Q5: What is the difference between a Container Restart and a Pod Replacement?
- **Short Verbal Answer**: *"In a container restart, the pod object, UID, IP address, and node assignment remain unchanged; Kubelet merely restarts the Linux container process within the existing cgroup, incrementing the restart count. In a pod replacement, the controller deletes the entire Pod object from the Kubernetes API, causing the Deployment to create a completely new pod with a fresh UID and IP address."*
- **Deep Technical Explanation**: Kubelet manages container restarts in-place via the local container runtime (Docker/containerd). Pod replacement is orchestrated at the cluster control plane level by the ReplicaSet controller.
- **Source Reference**: [app/controller/health_monitor.py:469](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L469) (`v1.delete_namespaced_pod`).

---

## Part 2: Linux Resources, Limits & Fault Injection

#### Q6: What is the difference between CPU Requests and CPU Limits in Kubernetes?
- **Short Verbal Answer**: *"A CPU request is used by the Kubernetes scheduler (`kube-scheduler`) to find a node with sufficient capacity, and sets the Linux cgroup `cpu.shares`. A CPU limit sets the hard CFS bandwidth quota (`cpu.cfs_quota_us`), throttling the process if it consumes more than its assigned time slices."*
- **Deep Technical Explanation**: If a pod requests 50m and limits to 200m on a node with spare capacity, it is guaranteed 50m and allowed to burst up to 200m. If it attempts to use 300m, Linux CFS throttles its execution; it is never killed.
- **Source Reference**: [k8s/deployment.yaml:78-84](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml#L78-L84) (`requests.cpu: 50m`, `limits.cpu: 200m`).

#### Q7: What is the difference between Memory Requests and Memory Limits?
- **Short Verbal Answer**: *"Memory requests guarantee reserved physical RAM for scheduling. Memory limits configure the Linux cgroup `memory.max` boundary. Because memory is incompressible, exceeding the limit triggers the Linux kernel Out-Of-Memory (OOM) killer to terminate the container."*
- **Deep Technical Explanation**: When a container exceeds its 128Mi limit, the kernel cgroup memory controller cannot page out anonymous memory to swap. It sends `SIGKILL` (ExitCode 137). Kubelet records `OOMKilled` in `last_state.terminated.reason`.
- **Source Reference**: [k8s/deployment.yaml:81-84](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml#L81-L84) (`limits.memory: 128Mi`).

#### Q8: How does your 1M CPU Workload button burn CPU? Is it simulated or real?
- **Short Verbal Answer**: *"It is 100% genuine computation. In `app/ui/app.py`, the endpoint executes an algorithmic loop: `for i in range(complexity): result += i * i`. This burns real CPU cycles inside the Python interpreter, which is observed immediately as a jump from 1m to 45m+ CPU via `kubectl top pods`."*
- **Deep Technical Explanation**: The Python interpreter executes bytecode operations in user-space, consuming CPU clock cycles on the host CPU core allocated to the container cgroup.
- **Source Reference**: [app/ui/app.py:137-147](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L137-L147) (`api_work()`).

#### Q9: What is the difference between bounded memory pressure and a genuine memory leak?
- **Short Verbal Answer**: *"A genuine memory leak is an uncontrolled, unbounded retention of heap memory caused by unreleased references or circular pointers that eventually crashes the process. Our controlled memory test allocates bounded memory up to a strict 50 MB safety ceiling, allowing memory pressure demonstration without destabilizing the node."*
- **Deep Technical Explanation**: Unbounded leaks cause silent memory exhaustion over hours or days. Our implementation deliberately bounds the allocation at 50 MB to guarantee predictable, safe live demonstration.
- **Source Reference**: [app/ui/app.py:168](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L168) (`if total_mb_currently + size_mb > 50: return 400`).

#### Q10: When you click Clear Memory, why doesn't OS resident memory (RSS) drop to zero immediately?
- **Short Verbal Answer**: *"In Python, clearing references causes the garbage collector to free heap objects within Python's memory pool (`pymalloc`). However, `pymalloc` and glibc `malloc` retain allocated virtual memory pages to satisfy future allocations rather than returning them to the OS kernel immediately."*
- **Deep Technical Explanation**: The OS kernel only reclaims memory if pages are explicitly unmapped via `munmap()` or madvised via `MADV_DONTNEED`. However, application-level telemetry and Prometheus gauges reset to zero immediately.
- **Source Reference**: [app/ui/app.py:160-164](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L160-L164), [app/ui/app.py:54](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L54) (`MEMORY_ALLOCATED.set(0)`).

#### Q11: What is the difference between CrashLoopBackOff and OOMKilled?
- **Short Verbal Answer**: *"OOMKilled is a specific termination reason where the Linux kernel killed the container for exceeding cgroup memory limits (ExitCode 137). CrashLoopBackOff is a Kubelet state where a container has crashed repeatedly, causing Kubelet to delay restart attempts exponentially (10s, 20s, 40s, up to 5m) to avoid thrashing."*
- **Deep Technical Explanation**: A container can enter CrashLoopBackOff due to application syntax errors, missing configuration secrets, unhandled exceptions, or repeated OOMKills.
- **Source Reference**: [app/controller/health_monitor.py:227-228](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L227-L228) (`BAD_POD_REASONS`).

#### Q12: How does your destructive crash injection work?
- **Short Verbal Answer**: *"It validates that `ENABLE_DESTRUCTIVE_CHAOS` is enabled and verifies the `X-Chaos-Token` header. If valid and not a dry-run, it spawns a background thread that sleeps 1 second and executes `os._exit(1)`. The Linux container process exits immediately, and Kubelet restarts it."*
- **Deep Technical Explanation**: Calling `os._exit(1)` bypasses Python's `try/finally` blocks and WSGI wrappers, issuing an immediate kernel `exit_group(1)` system call.
- **Source Reference**: [app/ui/app.py:329-378](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L329-L378) (`crash()`).

#### Q13: What is the purpose of Dry-Run Safety Mode in the chaos controls?
- **Short Verbal Answer**: *"Dry-Run mode tests the entire authentication, authorization, and logging pipeline without actually terminating the process or exhausting memory. It proves the safety gate is functional without introducing downtime during presentations."*
- **Deep Technical Explanation**: It inspects headers, validates tokens against `CHAOS_AUTH_TOKEN`, logs an audit entry, and returns HTTP 200 with `mode: 'dry_run'`.
- **Source Reference**: [app/ui/app.py:191-207](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L191-L207), [app/ui/app.py:340-350](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L340-L350).

---

## Part 3: Autonomous Monitoring & Self-Healing Engine

#### Q14: How does the controller detect an unhealthy pod?
- **Short Verbal Answer**: *"Every 30 seconds, the controller lists all pods in the `health-monitor` namespace. For each pod, `evaluate_pod()` checks if the phase is not `Running`, or if any container's `waiting.reason` is in `BAD_POD_REASONS` (`CrashLoopBackOff`, `ImagePullBackOff`, `OOMKilled`, etc.), or if total restarts exceed 5."*
- **Deep Technical Explanation**: It inspects the container status array in `pod.status.container_statuses` returned by the Kubernetes API.
- **Source Reference**: [app/controller/health_monitor.py:231-306](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L231-L306) (`evaluate_pod()`).

#### Q15: What is Controller Self-Protection and why is it essential?
- **Short Verbal Answer**: *"Self-protection prevents the controller from evaluating itself as an unhealthy target and deleting itself. Line 250 in `health_monitor.py` checks if the pod name contains `controller` or has label `app=health-monitor`. If true, it skips deletion to prevent cluster monitor suicide."*
- **Deep Technical Explanation**: In autonomous systems, recursive failure states can cause control planes to destroy themselves. Explicit self-exclusion guarantees supervisory continuity.
- **Source Reference**: [app/controller/health_monitor.py:249-251](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L249-L251), [app/controller/health_monitor.py:353-357](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L353-L357).

#### Q16: How does the controller resolve pod ownership?
- **Short Verbal Answer**: *"It reads `pod.metadata.owner_references` to find the parent ReplicaSet, and then reads the ReplicaSet's `owner_references` to find the parent Deployment name (`sample-app`). It never guesses names using string prefixes."*
- **Deep Technical Explanation**: Traversing the metadata directed acyclic graph (DAG) ensures that the pod is managed by a controller that will recreate it when deleted.
- **Source Reference**: [app/controller/health_monitor.py:309-330](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L309-L330) (`get_pod_owner_deployment()`).

#### Q17: What exact Kubernetes API action does the controller take to heal a pod?
- **Short Verbal Answer**: *"It calls `v1.delete_namespaced_pod(pod_name, namespace)`. It does not create pods directly; deleting the faulty pod causes the Kubernetes ReplicaSet controller to notice the replica count is below desired and automatically schedule a fresh replacement pod."*
- **Deep Technical Explanation**: This leverages the Kubernetes declarative reconciliation loop rather than reimplementing pod scheduling inside our custom controller.
- **Source Reference**: [app/controller/health_monitor.py:469](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L469).

#### Q18: How does the controller prevent infinite deletion loops?
- **Short Verbal Answer**: *"Through two mechanisms: a 60-second cooldown timer between recovery attempts on the same pod, and a hard retry ceiling of 3 attempts (`MAX_RECOVERY_ATTEMPTS`). If 3 attempts fail, it aborts auto-recovery and sends a CRITICAL alert requesting manual intervention."*
- **Deep Technical Explanation**: State is tracked in `_recovery_tracker[pod_name] = {"attempts": count, "last_attempt": timestamp}`.
- **Source Reference**: [app/controller/health_monitor.py:361-401](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L361-L401).

#### Q19: How do you verify that a replacement pod is actually healthy?
- **Short Verbal Answer**: *"Our `_wait_for_replacement()` function polls the namespace up to 120 seconds. It explicitly ignores the deleted pod UID, ignores terminating pods, verifies the candidate belongs to the target Deployment, and confirms that all container statuses have `ready == True`."*
- **Deep Technical Explanation**: We do not check for a generic 'Running' pod; we check that an actual replacement belonging to the workload has passed its readiness probes.
- **Source Reference**: [app/controller/health_monitor.py:536-582](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L536-L582) (`_wait_for_replacement()`).

#### Q20: What is the polling interval of your controller and why did you choose it?
- **Short Verbal Answer**: *"The controller polls every 30 seconds (`POLL_INTERVAL=30`). This provides responsive failure detection within half a minute while avoiding CPU and API server request throttling on single-node Minikube clusters."*
- **Deep Technical Explanation**: In production clusters, controllers can use Kubernetes Informers/Watch APIs (`watch.Watch()`) for event-driven updates. Polling was selected here for deterministic demonstration timing and low memory footprint.
- **Source Reference**: [app/controller/config.py:20](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/config.py#L20), [app/controller/health_monitor.py:728](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L728).

---

## Part 4: Observability, Metrics, HPA, Prometheus & Grafana

#### Q21: What is the difference between Kubernetes Metrics Server and Prometheus?
- **Short Verbal Answer**: *"Metrics Server is a lightweight, in-memory aggregator that provides real-time CPU and memory usage for `kubectl top` and HPA; it stores zero historical time-series data. Prometheus is a full time-series database that scrapes custom application metrics, evaluates PromQL queries, and stores historical metrics over days or weeks."*
- **Deep Technical Explanation**: Metrics Server implements the Kubernetes `metrics.k8s.io` API. Prometheus implements its own TSDB storage engine and PromQL query language.
- **Source Reference**: [k8s/metrics-server.yaml](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/metrics-server.yaml), [app/ui/app.py:51-55](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L51-L55).

#### Q22: How does the Horizontal Pod Autoscaler (HPA) make scaling decisions?
- **Short Verbal Answer**: *"HPA queries Metrics Server for the average CPU utilization across all pods in `sample-app`. When average utilization exceeds the 60% target relative to the pod's 50m request (i.e. > 30m), HPA uses the formula `desiredReplicas = ceil[currentReplicas * (currentUtilization / targetUtilization)]` to scale up to a maximum of 6 replicas."*
- **Deep Technical Explanation**: HPA also enforces a stabilization cooldown window (default: 5 minutes for scale down) to prevent rapid flapping or thrashing.
- **Source Reference**: [k8s/hpa.yaml:24-31](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/hpa.yaml#L24-L31).

#### Q23: Why didn't your 10-second traffic burst cause HPA to scale out immediately?
- **Short Verbal Answer**: *"HPA scales based on sustained average utilization over a 15-to-30 second scrape window, not brief 10-second bursts. A 10-second burst spikes CPU briefly, but the rolling average across both replicas remains below the 60% threshold over the evaluation window."*
- **Deep Technical Explanation**: This is an intentional stability feature of Kubernetes to prevent autoscalers from reacting aggressively to transient network spikes.
- **Source Reference**: `kubectl describe hpa sample-app-hpa -n health-monitor`.

#### Q24: What custom Prometheus metrics does your application export?
- **Short Verbal Answer**: *"Our application exports `http_requests_total` (Counter by method, endpoint, and status code), `http_request_duration_seconds` (Histogram by endpoint), `http_requests_in_progress` (Gauge), and `app_memory_allocated_bytes` (Gauge for allocated test memory)."*
- **Deep Technical Explanation**: Metrics are registered using the official `prometheus_client` Python library and exposed in text format at `/metrics`.
- **Source Reference**: [app/ui/app.py:51-55](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L51-L55), [app/ui/app.py:689-693](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L689-L693).

#### Q25: How does Grafana visualize this data?
- **Short Verbal Answer**: *"Grafana connects to Prometheus as a datasource (`http://prometheus:9090`). In `grafana-dashboard.json`, we defined 12 panels querying PromQL expressions like `sum(rate(http_requests_total[5m]))` and `container_cpu_usage_seconds_total` to graph request rates, latencies, error percentages, and CPU usage."*
- **Deep Technical Explanation**: Grafana executes PromQL range queries against Prometheus over HTTP and renders SVG/Canvas time-series charts.
- **Source Reference**: [dashboards/grafana-dashboard.json:76-81](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/dashboards/grafana-dashboard.json#L76-L81).

---

## Part 5: Email Notification Center, SMTP & Security

#### Q26: How does the email notification engine work?
- **Short Verbal Answer**: *"When an incident or test occurs, `send_notification()` formats a multipart MIME message (plain text + HTML). It reads credentials securely from Kubernetes Secret `smtp-credentials`, connects to `smtp.gmail.com:587`, executes STARTTLS encryption, authenticates, and dispatches the alert asynchronously."*
- **Deep Technical Explanation**: The dispatch is wrapped in a background thread to prevent SMTP network latency from blocking the main controller or HTTP server.
- **Source Reference**: [app/notifications.py:460-600](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L460-L600) (`_send_smtp_email()`).

#### Q27: What is the difference between `SMTP ACCEPTED` and inbox delivery?
- **Short Verbal Answer**: *"`SMTP ACCEPTED` proves that Google's mail relay server accepted our authenticated connection and took custody of the message with response code 250 OK. Inbox delivery is downstream: it depends on recipient spam filters, SPF/DKIM validation, and campus mail routing rules."*
- **Deep Technical Explanation**: SMTP is a store-and-forward protocol. Once the relay returns 250 OK, the sender's client responsibility ends; delivery to the final mailbox occurs asynchronously between mail transfer agents (MTAs).
- **Source Reference**: [app/notifications.py:325](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L325) (`smtp_relay_notice`).

#### Q28: How do you prevent email spam during an alert storm?
- **Short Verbal Answer**: *"We implement an in-memory alert deduplication cache with a 300-second cooldown window (`ALERT_COOLDOWN_SEC`). The cache key is composed of `f'{target_workload}:{event_type}'`. If an identical alert was sent within 5 minutes, delivery is suppressed."*
- **Deep Technical Explanation**: Suppressed alerts are logged in the SQLite audit table with status `suppressed` rather than creating redundant SMTP network traffic.
- **Source Reference**: [app/notifications.py:334-343](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L334-L343) (`_is_deduplicated()`).

#### Q29: How are credentials protected in your codebase and UI?
- **Short Verbal Answer**: *"Credentials are never committed to Git. In Kubernetes, they are injected from a Secret (`smtp-credentials`). On the frontend and API, `get_masked_config()` sanitizes usernames to `sa***@gmail.com` and passwords to `••••••••`. Raw passwords never appear in HTML, JSON, or logs."*
- **Deep Technical Explanation**: In `k8s/deployment.yaml`, environment variables use `valueFrom.secretKeyRef`.
- **Source Reference**: [app/notifications.py:261-275](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L261-L275), [k8s/deployment.yaml:64-76](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml#L64-L76).

#### Q30: Why did earlier screenshots show `NOT CONFIGURED`?
- **Short Verbal Answer**: *"Because before we provisioned the `smtp-credentials` Kubernetes Secret, `SMTP_USERNAME` was unset. Our backend truthfully detected missing credentials and labeled the status `NOT CONFIGURED` rather than fabricating a false connection."*
- **Deep Technical Explanation**: Once the Secret was mounted, the backend dynamically detected credentials and transitioned to `LIVE SMTP READY`.
- **Source Reference**: [app/notifications.py:287](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/notifications.py#L287) (`delivery_state = "NOT_CONFIGURED"`).

---

## Part 6: Persistence, Storage & Concurrency

#### Q31: How is incident data stored, and does it survive container restarts?
- **Short Verbal Answer**: *"Data is stored in an embedded SQLite database at `/app/data/incidents.db`. It survives container restarts because `/app/data` is mounted to a Kubernetes PersistentVolumeClaim (`incident-data-pvc`) backed by Minikube host storage."*
- **Deep Technical Explanation**: When a pod restarts, Kubelet remounts the PVC to the new container. SQLite files on persistent disk remain completely intact.
- **Source Reference**: [k8s/deployment.yaml:104-112](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml#L104-L112), [k8s/pvc.yaml](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/pvc.yaml).

#### Q32: How does SQLite handle concurrent writes from multiple pods or threads?
- **Short Verbal Answer**: *"In `incident_store.py`, all database access is mediated through a context manager (`get_db()`) configured with a 10-second busy timeout (`timeout=10.0`). Transactions execute with `IMMEDIATE` lock acquisition, committing and closing promptly to prevent lock contention."*
- **Deep Technical Explanation**: SQLite supports multiple concurrent readers and serializes writes. The 10-second timeout ensures that concurrent transactions wait rather than throwing `database is locked` errors.
- **Source Reference**: [app/ui/incident_store.py:25-31](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/incident_store.py#L25-L31) (`get_db()`).

#### Q33: What does the 'Reset All Faults' button actually do?
- **Short Verbal Answer**: *"It restores `app_state['is_ready'] = True`, clears all allocated test memory to 0 MB, and updates any active incidents in SQLite to `RECOVERED`. Crucially, it does NOT delete historical records, preserving the audit trail."*
- **Deep Technical Explanation**: It reconciles memory and readiness without deleting rows from `incidents`, `action_logs`, or `notifications` tables.
- **Source Reference**: [app/ui/app.py:303-328](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L303-L328) (`chaos_reset()`).

---

## Part 7: Security, RBAC & Cluster Permissions

#### Q34: What permissions does your controller have in Kubernetes?
- **Short Verbal Answer**: *"Our controller operates under least-privilege RBAC. In `k8s/role.yaml`, it has permission to `delete`, `get`, `list`, and `watch` pods, and read-only access to `nodes`, `events`, and `deployments`. It cannot modify Secrets, create namespaces, or change cluster configs."*
- **Deep Technical Explanation**: Restricting write access strictly to pod deletion prevents a compromised controller from modifying deployment manifests or escalating privileges.
- **Source Reference**: [k8s/role.yaml:20-39](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/role.yaml#L20-L39).

#### Q35: Why did the controller use a ClusterRole instead of a namespace Role?
- **Short Verbal Answer**: *"Because node health checking (`check_nodes()`) evaluates `NodeNotReady` and `NodeMemoryPressure`. In Kubernetes, `Node` is a cluster-scoped resource that does not belong to any namespace, requiring a ClusterRole."*
- **Deep Technical Explanation**: While pod operations are confined to `health-monitor`, evaluating node conditions requires cluster-level read permissions.
- **Source Reference**: [app/controller/health_monitor.py:587-633](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L587-L633) (`check_nodes()`).

#### Q36: How does the application pod authenticate with the Kubernetes API?
- **Short Verbal Answer**: *"Via the in-cluster service account token automatically mounted by Kubernetes at `/var/run/secrets/kubernetes.io/serviceaccount/token`. The Python client reads this token via `k8s_config.load_incluster_config()`."*
- **Deep Technical Explanation**: The API server validates the JWT token against the `health-monitor-sa` ServiceAccount and checks RBAC permissions via the API server authorizer.
- **Source Reference**: [app/ui/app.py:496](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L496), [k8s/deployment.yaml:29](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/k8s/deployment.yaml#L29) (`serviceAccountName: health-monitor-sa`).

---

## Part 8: Edge Cases, Limitations & Future Improvements

#### Q37: What is the primary limitation of running this system on a single-node Minikube cluster?
- **Short Verbal Answer**: *"In a single-node cluster, automated node failover is impossible. If the Minikube node enters `NotReady` or suffers hardware failure, Kubernetes cannot reschedule pods to another node because no other node exists."*
- **Deep Technical Explanation**: Our code explicitly logs this limitation in [health_monitor.py:605](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L605): `Single-node cluster limitation: Automated node migration unavailable`. In multi-node production clusters, node taints and pod eviction automatically reschedule workloads.

#### Q38: What would happen if a pod enters CrashLoopBackOff due to a permanent code bug or missing Secret?
- **Short Verbal Answer**: *"Deleting the pod would cause the new pod to crash for the same reason. Our controller detects this via `MAX_RECOVERY_ATTEMPTS=3`. After 3 failed attempts, it aborts auto-deletion, marks the incident `FAILED`, and sends a critical alert to human operators."*
- **Deep Technical Explanation**: This prevents the controller from creating an infinite deletion loop that floods the cluster and consumes CPU.
- **Source Reference**: [app/controller/health_monitor.py:369-401](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L369-L401).

#### Q39: Is there any race condition between the Kubelet restart loop and your custom controller?
- **Short Verbal Answer**: *"Yes, if a container crashes, Kubelet restarts it within seconds, while our controller polls every 30 seconds. We resolved this in line 273 of `health_monitor.py`: if Kubelet successfully restarts the container and it reaches Running and Ready, our controller detects `k8s_recovered_restart` and skips redundant pod deletion."*
- **Deep Technical Explanation**: This is an intentional cooperative design: let low-level Kubelet handle fast container restarts, and let the custom controller handle stuck states (`CrashLoopBackOff`, bad probes, or unready hung pods).
- **Source Reference**: [app/controller/health_monitor.py:270-274](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L270-L274), [app/controller/health_monitor.py:698-715](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/controller/health_monitor.py#L698-L715).

#### Q40: What happens if two replicas of sample-app are running and readiness is toggled on one?
- **Short Verbal Answer**: *"Traffic to that specific pod stops immediately because its IP is removed from endpoints. The second pod continues serving user traffic normally, demonstrating high-availability load balancing."*
- **Deep Technical Explanation**: Kube-proxy only routes traffic to healthy endpoints. Since replica 2 is still ready, users experience zero downtime.
- **Source Reference**: `kubectl get endpoints sample-app-service -n health-monitor`.

#### Q41: Why does `app.py` use a global dictionary `app_state` for readiness?
- **Short Verbal Answer**: *"Because each pod runs as an independent OS process. The readiness state represents that specific container's health. In production microservices, this is equivalent to an in-memory health flag or circuit breaker."*
- **Deep Technical Explanation**: Each container maintains its own local memory space. Toggling readiness in pod A isolates pod A without affecting pod B.
- **Source Reference**: [app/ui/app.py:57-60](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/app/ui/app.py#L57-L60).

#### Q42: How would you scale this architecture to monitor 100 microservices across multiple namespaces?
- **Short Verbal Answer**: *"We would transition the controller from active polling to Kubernetes Informers (`client.watch.Watch()`), configure `WATCH_NAMESPACE=''` to monitor all namespaces, distribute alerting via Kafka or Celery task queues, and persist incidents in a distributed PostgreSQL cluster."*
- **Deep Technical Explanation**: Informers maintain an in-memory client cache synchronized via the Kubernetes Watch API HTTP stream, eliminating polling overhead.

#### Q43: How do you prove that your unit tests pass?
- **Short Verbal Answer**: *"Our project includes 56 automated unit tests across controller logic, incident persistence, notification dispatching, and UI routes. Running `py -m unittest discover tests` executes all 56 tests in approximately 10 seconds with 100% pass rate."*
- **Deep Technical Explanation**: Tests use mocks for Kubernetes APIs and SMTP connections, validating error handling and status transitions deterministically.
- **Source Reference**: [tests/test_controller.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/tests/test_controller.py), [tests/test_notifications.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/tests/test_notifications.py), [tests/test_ui.py](file:///c:/Users/Administrator/Desktop/Projects/auto-kubernetes-health-monitor/tests/test_ui.py).

#### Q44: What would you do if Google SMTP service is blocked or deprecates App Passwords?
- **Short Verbal Answer**: *"Our notification architecture is modular. By changing `ALERT_BACKEND='telegram'`, alerts route via Telegram Bot API over HTTPS. Alternatively, we can configure an internal SMTP relay like Postfix or AWS SES simply by updating `SMTP_HOST` in `.env` without modifying code."*
- **Deep Technical Explanation**: `notifications.py` abstracts transport behind `NotificationConfig` and `send_notification()`.

#### Q45: What is your personal key takeaway from building this project?
- **Short Verbal Answer**: *"My greatest takeaway is understanding the distinction between observability and autonomous control. Monitoring tells you that a system is broken; a true self-healing control plane understands workload ownership, validates recovery criteria, and prevents cascading failures through cooldowns and safety boundaries."*
