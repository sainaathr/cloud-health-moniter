from flask import Flask, render_template, request, jsonify, Response
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
import time
import os
import sys
import datetime

_ui_dir = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_ui_dir, "..", ".."))

# If in local repo structure, ensure repo root is prioritized
if os.path.exists(os.path.join(_repo_root, "app")):
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

# Ensure local dir is accessible as fallback for containerized execution
if _ui_dir not in sys.path:
    sys.path.append(_ui_dir)

try:
    from app.ui import incident_store
except ImportError:
    import incident_store

try:
    from app.notifications import NotificationConfig, send_notification, send_test_notification
except ImportError:
    from notifications import NotificationConfig, send_notification, send_test_notification

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# --- Configuration & Safety Controls ---
# Destructive operations MUST be explicitly enabled via environment variable
ENABLE_DESTRUCTIVE_CHAOS = os.getenv("ENABLE_DESTRUCTIVE_CHAOS", "false").lower() == "true"
CHAOS_AUTH_TOKEN = os.getenv("CHAOS_AUTH_TOKEN", "k8s-chaos-admin-secure")

# --- Prometheus Metrics ---
from prometheus_client import REGISTRY

def _get_or_create_metric(collector_cls, name, documentation, *args, **kwargs):
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return collector_cls(name, documentation, *args, **kwargs)

REQUEST_COUNT = _get_or_create_metric(Counter, 'http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status_code'])
REQUEST_LATENCY = _get_or_create_metric(Histogram, 'http_request_duration_seconds', 'HTTP request latency in seconds', ['method', 'endpoint'])
IN_PROGRESS = _get_or_create_metric(Gauge, 'http_requests_in_progress', 'Concurrent HTTP requests in progress', ['method', 'endpoint'])
MEMORY_ALLOCATED = _get_or_create_metric(Gauge, 'app_memory_allocated_bytes', 'Bounded memory allocated for testing')

# Global state
app_state = {
    "is_ready": True,
    "memory_leak": []
}

@app.before_request
def before_request():
    request.start_time = time.time()
    IN_PROGRESS.labels(method=request.method, endpoint=request.path).inc()

@app.after_request
def after_request(response):
    request_latency = time.time() - getattr(request, 'start_time', time.time())
    REQUEST_LATENCY.labels(method=request.method, endpoint=request.path).observe(request_latency)
    REQUEST_COUNT.labels(method=request.method, endpoint=request.path, status_code=response.status_code).inc()
    IN_PROGRESS.labels(method=request.method, endpoint=request.path).dec()
    return response

def _check_destructive_authorization():
    """
    Validates server-side enablement and genuine authorization header.
    Destructive operations MUST NEVER be triggered by a query parameter, form field, or checkbox alone.
    """
    # 1. Server-side policy gate
    if not ENABLE_DESTRUCTIVE_CHAOS:
        incident_store.log_action("WARN", "ChaosAPI", "Blocked destructive chaos attempt: ENABLE_DESTRUCTIVE_CHAOS is disabled")
        return False, jsonify({
            "status": "forbidden",
            "error": "Destructive operations are disabled by server policy. Set ENABLE_DESTRUCTIVE_CHAOS=true in server environment to enable."
        }), 403

    # 2. Server-side authorization token gate
    # Accept token strictly from headers: X-Chaos-Token or Authorization: Bearer <token>
    auth_header = request.headers.get("X-Chaos-Token") or request.headers.get("Authorization")
    token = None
    if auth_header:
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = auth_header.strip()

    if not token or token != CHAOS_AUTH_TOKEN:
        incident_store.log_action("WARN", "ChaosAPI", "Blocked unauthorized destructive chaos attempt (invalid or missing token)")
        return False, jsonify({
            "status": "unauthorized",
            "error": "Genuine server-side authorization required. Provide valid token in 'X-Chaos-Token' header."
        }), 401

    return True, None, 200

# --- Endpoints ---

@app.route('/')
def index():
    pod_name = os.getenv('HOSTNAME', 'Unknown')
    return render_template('index.html', pod_name=pod_name, is_ready=app_state['is_ready'])

@app.route('/health/live')
def health_live():
    """Liveness probe: confirms the process is running and responding."""
    return jsonify({"status": "alive"}), 200

@app.route('/health/ready')
def health_ready():
    """Readiness probe: checks if app can serve traffic."""
    if app_state["is_ready"]:
        return jsonify({"status": "ready"}), 200
    else:
        return jsonify({"status": "not_ready"}), 503

@app.route('/api/status')
def api_status():
    """Returns application status and simple stats."""
    total_bytes = sum(len(s) for s in app_state["memory_leak"])
    return jsonify({
        "pod_name": os.getenv('HOSTNAME', 'Unknown'),
        "is_ready": app_state["is_ready"],
        "memory_allocated_bytes": total_bytes
    })

@app.route('/api/work')
def api_work():
    """Performs bounded CPU work."""
    complexity = int(request.args.get('complexity', 100))
    complexity = min(complexity, 5000000)
    
    result = 0
    for i in range(complexity):
        result += i * i
        
    return jsonify({"message": f"Completed work with complexity {complexity}", "result": result})

@app.route('/api/memory-test')
def memory_test():
    """Allocates bounded memory safely (limit 50MB). Clears it if size_mb=0."""
    try:
        size_mb = int(request.args.get('size_mb', 10))
    except ValueError:
        return jsonify({"message": "invalid size_mb parameter"}), 400
    
    if size_mb < 0:
        return jsonify({"message": "size_mb cannot be negative"}), 400

    if size_mb == 0:
        app_state["memory_leak"].clear()
        MEMORY_ALLOCATED.set(0)
        incident_store.log_action("INFO", "Simulation", "Allocated test memory cleared to 0MB")
        return jsonify({"message": "Memory cleared", "memory_allocated_bytes": 0})
    
    total_mb_currently = sum(len(s) for s in app_state["memory_leak"]) // (1024 * 1024)
    
    if total_mb_currently + size_mb > 50:
        return jsonify({"message": f"Allocation rejected. Current: {total_mb_currently}MB, Target: {total_mb_currently + size_mb}MB, Limit: 50MB"}), 400

    # Allocate string
    app_state["memory_leak"].append('a' * 1024 * 1024 * size_mb)
    
    total_bytes = sum(len(s) for s in app_state["memory_leak"])
    MEMORY_ALLOCATED.set(total_bytes)
    total_mb = total_bytes // (1024 * 1024)
    incident_store.log_action("INFO", "Simulation", f"Allocated {size_mb}MB memory chunk. Current total: {total_mb}MB")
    return jsonify({"message": f"Allocated {size_mb} MB. Total allocation: {total_mb} MB", "memory_allocated_bytes": total_bytes})

@app.route('/api/chaos/oom', methods=['POST'])
def chaos_oom():
    """
    Deliberate memory exhaustion to trigger OOMKilled.
    Strictly gated: requires ENABLE_DESTRUCTIVE_CHAOS=true and X-Chaos-Token.
    Supports dry-run verification so safety gate can be tested without actually exhausting memory.
    """
    authorized, err_resp, code = _check_destructive_authorization()
    if not authorized:
        return err_resp, code

    is_dry_run = (
        request.headers.get("X-Dry-Run", "false").lower() == "true" or
        request.args.get("dry_run", "false").lower() == "true" or
        (request.is_json and request.json.get("dry_run") is True)
    )

    pod_name = os.getenv('HOSTNAME', 'Unknown')
    is_in_k8s = os.path.exists('/var/run/secrets/kubernetes.io') or ('sample-app' in pod_name)

    if is_dry_run:
        incident_store.log_action("INFO", "ChaosAPI", "Destructive OOM safety gate verified via dry-run mode.")
        return jsonify({
            "status": "success",
            "mode": "dry_run",
            "message": "Destructive OOM authorization verified. Safety gate passed without memory exhaustion.",
            "pod_name": pod_name
        }), 200

    incident = incident_store.record_incident(
        incident_type="OOM Memory Exhaustion",
        target_pod=pod_name,
        detection_source="Chaos API (Authorized)",
        status="TRIGGERED",
        recovery_action="Kubernetes OOMKilled container restart",
        evidence="Attempted allocation: 200MB (Pod memory limit is 128Mi)",
        category="NATIVE_K8S" if is_in_k8s else "SIMULATION",
        is_simulated=not is_in_k8s
    )

    # Deliberately exhaust memory (200MB) only if authorized and not dry run
    app_state["memory_leak"].append('a' * 1024 * 1024 * 200)
    total_bytes = sum(len(s) for s in app_state["memory_leak"])
    MEMORY_ALLOCATED.set(total_bytes)
    return jsonify({
        "status": "triggered",
        "message": "Triggered memory over-allocation",
        "incident_id": incident.get("incident_id") if incident else "INC-OOM",
        "allocated_mb": total_bytes // (1024 * 1024)
    })

@app.route('/api/chaos/readiness', methods=['POST'])
def toggle_readiness():
    """Toggles readiness state and records persistent incident with explicit classification."""
    pod_name = os.getenv('HOSTNAME', 'Unknown')
    is_in_k8s = os.path.exists('/var/run/secrets/kubernetes.io') or ('sample-app' in pod_name)
    app_state["is_ready"] = not app_state["is_ready"]
    new_state = app_state["is_ready"]

    category = "K8S_READINESS" if is_in_k8s else "SIMULATION"
    is_sim = not is_in_k8s

    if not new_state:
        incident = incident_store.record_incident(
            incident_type="Readiness Failure (HTTP 503)" if is_in_k8s else "Simulation: Readiness Failure (HTTP 503)",
            target_pod=pod_name,
            detection_source="Readiness Probe (/health/ready)",
            status="UNREADY",
            recovery_action="Kubernetes Service traffic diversion (endpoints isolated)" if is_in_k8s else "Simulated service endpoint isolation",
            recovery_result="TRAFFIC_ISOLATED",
            evidence="Readiness toggle set to False; /health/ready returns HTTP 503",
            category=category,
            is_simulated=is_sim
        )
        msg = f"Readiness set to False. Pod {pod_name} isolated from Service endpoints."
        inc_id = incident.get("incident_id") if incident else None
        try:
            from app.notifications import send_notification
            send_notification(
                event_type="UNHEALTHY_PROBE",
                incident_id=inc_id or "INC-READINESS-FAIL",
                target_workload="sample-app",
                target_pod=pod_name,
                problem="Readiness probe failed (/health/ready HTTP 503)",
                action_taken="Pod isolated from Kubernetes Service traffic endpoints",
                status="UNREADY",
                severity="WARNING"
            )
        except Exception:
            pass
    else:
        # Recover active readiness incidents
        incidents = incident_store.get_incidents(limit=20)
        for inc in incidents:
            if "Readiness Failure" in inc["incident_type"] and inc["status"] != "RECOVERED":
                incident_store.update_incident(
                    inc["incident_id"],
                    status="RECOVERED",
                    recovery_action="Readiness restored; Pod re-added to Service endpoints",
                    recovery_result="SUCCESS",
                    evidence="Readiness toggle restored to True; /health/ready returns HTTP 200",
                    category=category,
                    is_simulated=is_sim
                )
        msg = f"Readiness restored. Pod {pod_name} re-added to Service endpoints."
        inc_id = None
        try:
            from app.notifications import send_notification
            send_notification(
                event_type="RECOVERY_VERIFIED",
                incident_id="INC-READINESS-RESTORED",
                target_workload="sample-app",
                target_pod=pod_name,
                problem="Readiness failure recovered",
                action_taken="Readiness restored; Pod re-added to Service endpoints",
                status="RECOVERED",
                severity="INFO"
            )
        except Exception:
            pass

    return jsonify({"message": msg, "is_ready": new_state, "incident_id": inc_id, "category": category})

@app.route('/api/chaos/reset', methods=['POST'])
def chaos_reset():
    """Restores readiness and clears all allocated memory."""
    pod_name = os.getenv('HOSTNAME', 'Unknown')
    app_state["is_ready"] = True
    app_state["memory_leak"].clear()
    MEMORY_ALLOCATED.set(0)

    incidents = incident_store.get_incidents(limit=20)
    for inc in incidents:
        if inc["status"] in ("UNREADY", "TRIGGERED", "SIMULATED_FAILURE"):
            incident_store.update_incident(
                inc["incident_id"],
                status="RECOVERED",
                recovery_action="Manual Reset Action",
                recovery_result="SUCCESS",
                evidence="Readiness restored to True and memory cleared"
            )
    incident_store.log_action("SUCCESS", "Simulation", f"Reset completed on {pod_name}: readiness=True, memory=0MB")
    return jsonify({
        "status": "success",
        "message": "System state reset: readiness is READY and test memory cleared.",
        "is_ready": True,
        "memory_allocated_bytes": 0
    })

@app.route('/api/chaos/crash', methods=['POST'])
def crash():
    """
    Deliberately triggers or simulates a pod crash with auto-healing.
    Strictly gated: requires ENABLE_DESTRUCTIVE_CHAOS=true and valid X-Chaos-Token.
    Supports dry-run testing. Never executes destructive pod deletion on the host cluster.
    """
    authorized, err_resp, code = _check_destructive_authorization()
    if not authorized:
        return err_resp, code

    is_dry_run = (
        request.headers.get("X-Dry-Run", "false").lower() == "true" or
        request.args.get("dry_run", "false").lower() == "true" or
        (request.is_json and request.json.get("dry_run") is True)
    )
    if is_dry_run:
        return jsonify({
            "status": "success",
            "mode": "dry_run",
            "message": "Destructive crash authorization verified. Safety dry-run completed successfully without disruption."
        }), 200

    # Check if running inside actual Kubernetes container
    is_in_k8s_container = os.path.exists('/var/run/secrets/kubernetes.io') or (os.getenv('HOSTNAME') and 'sample-app' in os.getenv('HOSTNAME'))

    if is_in_k8s_container:
        pod_name = os.getenv('HOSTNAME', 'Unknown')
        incident = incident_store.record_incident(
            incident_type="Fatal Application Crash (os._exit)",
            target_pod=pod_name,
            detection_source="Kubelet Liveness / Process Watcher",
            status="TRIGGERED",
            recovery_action="Kubernetes container restart / controller watch",
            recovery_result="PENDING_RESTART",
            evidence="Process exited via deliberate os._exit(1) call",
            category="NATIVE_K8S",
            is_simulated=False
        )
        def kill_pod():
            time.sleep(1)
            os._exit(1)
        import threading
        threading.Thread(target=kill_pod).start()
        return jsonify({
            "status": "success",
            "message": f"Pod {pod_name} is terminating in 1 second. Kubelet and Controller will auto-heal it.",
            "target_pod": pod_name,
            "incident_id": incident.get("incident_id") if incident else "INC-CRASH"
        })

    # Standalone / simulated mode:
    # Safe simulation — does NOT delete live cluster resources
    pod_name = "sample-app-simulated-pod"
    incident = incident_store.record_incident(
        incident_type="Simulated Pod Crash",
        target_pod=pod_name,
        detection_source="Chaos API (Simulation)",
        status="SIMULATED_FAILURE",
        recovery_action="Automated self-healing simulation timer",
        recovery_result="PENDING_HEAL",
        evidence="Simulated pod crash; self-healing timer engaged",
        category="SIMULATION",
        is_simulated=True
    )
    app_state["is_ready"] = False

    def auto_heal():
        time.sleep(8)
        app_state["is_ready"] = True
        if incident and incident.get("incident_id"):
            incident_store.update_incident(
                incident["incident_id"],
                status="SIMULATED_RECOVERED",
                recovery_action="Autonomous Self-Healing Engine (Simulated)",
                recovery_result="SUCCESS",
                evidence="Simulated pod auto-healed and readiness probe restored to 200 OK",
                category="SIMULATION",
                is_simulated=True
            )
        incident_store.log_action("SUCCESS", "SelfHealing", f"Simulated pod {pod_name} successfully auto-healed after crash.")

    import threading
    threading.Thread(target=auto_heal).start()

    return jsonify({
        "status": "success",
        "message": f"Crash simulated on '{pod_name}'. Readiness dropped to 503; autonomous self-healing is recovering the pod in 8s.",
        "target_pod": pod_name,
        "incident_id": incident.get("incident_id") if incident else "INC-CRASH"
    })

@app.route('/api/incidents', methods=['GET'])
def api_incidents():
    """Fetch persistent incident timeline, with optional category filter."""
    limit = int(request.args.get('limit', 50))
    category = request.args.get('category')
    return jsonify({"incidents": incident_store.get_incidents(limit=limit, category=category)})

@app.route('/api/logs', methods=['GET'])
def api_logs():
    """Fetch recent action logs."""
    limit = int(request.args.get('limit', 100))
    return jsonify({"logs": incident_store.get_action_logs(limit=limit)})

# ── Notification Center Endpoints ─────────────────────────────────────────────
@app.route('/api/notifications/status', methods=['GET'])
def api_notification_status():
    """Returns current notification channel status with secrets masked."""
    try:
        cfg = NotificationConfig.get_masked_config()
        summary = incident_store.get_notification_summary() if incident_store else {}
        return jsonify({
            "status": "success",
            "config": cfg,
            "summary": summary
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to read notification config: {e}"}), 500

@app.route('/api/notifications/history', methods=['GET'])
def api_notification_history():
    """Returns recent notification delivery history from persistent store."""
    limit = int(request.args.get('limit', 50))
    incident_id = request.args.get('incident_id')
    return jsonify({"notifications": incident_store.get_notifications(limit=limit, incident_id=incident_id)})

@app.route('/api/notifications/test', methods=['POST'])
def api_notification_test():
    """
    Triggers a test notification validating delivery channels.
    Supports ?live=true or JSON {"live": true} to test live delivery to sainaath.r2025@vitstudent.ac.in.
    """
    try:
        force_live = request.args.get('live', '').strip().lower() in ('1', 'true', 'yes')
        if not force_live and request.is_json:
            data = request.get_json(silent=True) or {}
            force_live = bool(data.get('live', False))

        result = send_test_notification(force_live=force_live)
        return jsonify({
            "status": "success",
            "result": result,
            "message": f"Notification test completed: status={result.get('status', 'unknown')}"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to dispatch test notification: {e}"}), 500

@app.route('/api/cluster/overview', methods=['GET'])
def cluster_overview():
    """
    Returns live cluster overview or an explicit unavailable state.
    NEVER reports a fabricated Running pod or healthy cluster when live data is unavailable.
    """
    k8s_connected = False
    controller_status = "UNAVAILABLE"
    hpa_info = {"target_cpu": 60, "current_cpu": None, "status": "Cluster Unavailable"}
    desired_replicas = 0
    available_replicas = 0
    h_count = 0
    unh_count = 0
    total_pods = 0
    cluster_error = None

    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
            k8s_connected = True
        except Exception:
            k8s_config.load_kube_config()
            k8s_connected = True
    except Exception as e:
        k8s_connected = False
        cluster_error = str(e)

    if k8s_connected:
        try:
            v1 = client.CoreV1Api()
            apps_v1 = client.AppsV1Api()
            auto_v2 = client.AutoscalingV2Api()

            pods = v1.list_namespaced_pod("health-monitor", _request_timeout=(2, 2)).items
            for p in pods:
                p_name = p.metadata.name or ""
                p_phase = p.status.phase or "Unknown"
                statuses = p.status.container_statuses or []
                p_ready = bool(statuses and all(cs.ready for cs in statuses))
                if "controller" in p_name:
                    controller_status = "RUNNING" if (p_phase == "Running" and p_ready) else "UNHEALTHY"
                else:
                    if p_phase == "Running" and p_ready:
                        h_count += 1
                    else:
                        unh_count += 1

            try:
                dept = apps_v1.read_namespaced_deployment("sample-app", "health-monitor", _request_timeout=(2, 2))
                desired_replicas = dept.spec.replicas or 0
                available_replicas = dept.status.available_replicas or 0
            except Exception:
                desired_replicas = 0
                available_replicas = 0

            try:
                hpa = auto_v2.read_namespaced_horizontal_pod_autoscaler("sample-app-hpa", "health-monitor", _request_timeout=(2, 2))
                if hpa.status and hpa.status.current_metrics:
                    for cm in hpa.status.current_metrics:
                        if cm.resource and cm.resource.current and cm.resource.current.average_utilization is not None:
                            hpa_info["current_cpu"] = cm.resource.current.average_utilization
                            hpa_info["status"] = "Active"
                else:
                    hpa_info["status"] = "Active (Awaiting metrics scrape)"
            except Exception:
                hpa_info["status"] = "HPA resource not queried"

            data_source = "Live Kubernetes API"
            total_pods = h_count + unh_count
        except Exception as e:
            k8s_connected = False
            data_source = "Kubernetes API Unavailable"
            controller_status = "UNAVAILABLE"
            cluster_error = f"K8s API query failed: {str(e)[:60]}"
            h_count = 0
            unh_count = 0
            total_pods = 0
            desired_replicas = 0
            available_replicas = 0
            hpa_info = {"target_cpu": 60, "current_cpu": None, "status": "Cluster Unavailable"}
    else:
        data_source = "Kubernetes API Unavailable"
        controller_status = "UNAVAILABLE"
        cluster_error = "Kubernetes cluster is not connected or unreachable."
        h_count = 0
        unh_count = 0
        total_pods = 0
        desired_replicas = 0
        available_replicas = 0
        hpa_info = {"target_cpu": 60, "current_cpu": None, "status": "Cluster Unavailable"}

    active_incidents = [i for i in incident_store.get_incidents(10) if i["status"] not in ("RECOVERED", "SUCCESS", "SIMULATED_RECOVERED")]

    return jsonify({
        "cluster_status": "AVAILABLE" if k8s_connected else "UNAVAILABLE",
        "app_health": "HEALTHY" if app_state["is_ready"] else "DEGRADED",
        "controller_status": controller_status,
        "pods_healthy": h_count,
        "pods_unhealthy": unh_count,
        "pods_total": total_pods,
        "replicas_desired": desired_replicas,
        "replicas_available": available_replicas,
        "hpa": hpa_info,
        "data_source": data_source,
        "k8s_connected": k8s_connected,
        "cluster_error": cluster_error,
        "last_health_check": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active_incident_count": len(active_incidents),
        "memory_allocated_bytes": sum(len(s) for s in app_state["memory_leak"])
    })

@app.route('/api/cluster/pods', methods=['GET'])
def cluster_pods():
    """
    Returns pod details from Kubernetes or an explicit unavailable error state.
    Never fabricates a running pod or healthy cluster when live data is unavailable.
    """
    pod_list = []
    include_simulated = request.args.get('include_simulated', 'false').lower() == 'true'

    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod("health-monitor", _request_timeout=(2, 2)).items
        source = "Live Kubernetes API"
        for p in pods:
            statuses = p.status.container_statuses or []
            restarts = sum(cs.restart_count or 0 for cs in statuses)
            is_ready = bool(statuses and all(cs.ready for cs in statuses))
            term_reason = ""
            for cs in statuses:
                if cs.last_state and cs.last_state.terminated:
                    term_reason = cs.last_state.terminated.reason or ""
            pod_list.append({
                "name": p.metadata.name,
                "workload": p.metadata.labels.get("app", "unknown") if p.metadata.labels else "unknown",
                "phase": p.status.phase or "Unknown",
                "ready": is_ready,
                "restarts": restarts,
                "node": p.spec.node_name or "minikube",
                "termination_reason": term_reason,
                "created_at": str(p.metadata.creation_timestamp) if p.metadata.creation_timestamp else "N/A"
            })
        return jsonify({
            "pods": pod_list,
            "source": source,
            "k8s_connected": True,
            "count": len(pod_list)
        })
    except Exception as e:
        if include_simulated:
            return jsonify({
                "pods": [{
                    "name": "sample-app-simulated-pod",
                    "workload": "sample-app (Simulated)",
                    "phase": "Simulated",
                    "ready": app_state["is_ready"],
                    "restarts": 0,
                    "node": "simulation-host",
                    "termination_reason": "",
                    "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }],
                "source": "Local Simulation",
                "k8s_connected": False,
                "count": 1
            })

        # Explicit unavailable error state
        return jsonify({
            "pods": [],
            "source": "Unavailable",
            "k8s_connected": False,
            "count": 0,
            "error": f"Kubernetes cluster is unavailable or unreachable ({str(e)[:60]})."
        })

@app.route('/api/traffic/generate', methods=['POST'])
def traffic_generate():
    """Trigger a bounded, safe background traffic burst."""
    import urllib.request
    import threading

    duration = min(int(request.args.get('duration', 10)), 30)
    workers = min(int(request.args.get('workers', 5)), 15)

    incident_store.log_action("INFO", "Traffic", f"Starting load burst: {workers} workers for {duration}s")

    def run_worker():
        end_time = time.time() + duration
        url = request.host_url + "api/work?complexity=50000"
        while time.time() < end_time:
            try:
                urllib.request.urlopen(url, timeout=2)
            except Exception:
                pass
            time.sleep(0.05)

    for _ in range(workers):
        threading.Thread(target=run_worker, daemon=True).start()

    return jsonify({
        "status": "started",
        "message": f"Traffic generation started with {workers} threads for {duration} seconds.",
        "duration_sec": duration,
        "workers": workers
    })

@app.route('/metrics')
def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
