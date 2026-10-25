"""
health_monitor.py — Core Kubernetes Health Monitor & Self-Healing Controller
=============================================================================
This Python application uses the official Kubernetes Python client to
continuously watch pods and nodes inside a Kubernetes cluster.

HOW IT WORKS (high level):
  1. Connect to the cluster (in-cluster config or local kubeconfig).
  2. Every POLL_INTERVAL seconds, list all pods in WATCH_NAMESPACE.
  3. For each pod, evaluate its phase and container statuses.
  4. If a pod is unhealthy, check if it is owned by a Deployment.
  5. If yes, attempt self-healing (delete the pod so Kubernetes recreates it).
  6. Apply cooldown and retry limits to avoid infinite loops.
  7. Log every event to stdout AND to a log file.
  8. Send optional alerts via Telegram.
  9. Record persistent structured incidents distinguishing:
     - Native Kubernetes restarts
     - Controller healing actions
     - Verified replacement pod ready states
"""

import time
import logging
import os
import sys
import json
import datetime
from collections import defaultdict
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

try:
    import config  # our local config.py
except ImportError:
    from app.controller import config

# Try importing incident_store for persistent telemetry if available
try:
    import incident_store
except ImportError:
    try:
        from app.ui import incident_store
    except ImportError:
        incident_store = None

# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging():
    """Configure logging to both console and a rotating log file."""
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler(sys.stdout)]

    # Only add file handler if we can reach the log directory
    log_dir = os.path.dirname(config.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError:
            pass  # running locally without /app/logs — stdout only

    if os.path.exists(log_dir) or not log_dir:
        try:
            handlers.append(logging.FileHandler(config.LOG_FILE))
        except OSError:
            pass  # cannot write log file — that is okay

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
    )

setup_logging()
logger = logging.getLogger("health-monitor")

# ── Kubernetes client ─────────────────────────────────────────────────────────

def connect_to_cluster():
    """
    Try to use in-cluster config (running inside a Pod).
    Fall back to local kubeconfig for development on your laptop.
    """
    try:
        k8s_config.load_incluster_config()
        logger.info("Connected using IN-CLUSTER config (running inside Kubernetes).")
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        logger.info("Connected using LOCAL kubeconfig (~/.kube/config).")

try:
    from app.notifications import send_notification
except ImportError:
    try:
        from notifications import send_notification
    except ImportError:
        send_notification = None

# ── Alerting ──────────────────────────────────────────────────────────────────

def send_alert(message: str,
               event_type: str = "ALERT",
               incident_id: str = "",
               target_workload: str = "sample-app",
               target_pod: str = "unknown",
               namespace: str = "health-monitor",
               problem: str = "",
               action_taken: str = "",
               status: str = "TRIGGERED",
               severity: str = "WARNING",
               details: Optional[str] = None,
               metrics: Optional[dict] = None,
               thresholds: Optional[dict] = None,
               non_blocking: bool = True):
    """
    Send structured alert via notifications manager with Telegram fallback.
    Dispatches asynchronously in a background thread by default so network I/O
    never impedes the controller's main monitoring loop.
    """
    def _do_send():
        # Dynamically lookup send_notification to respect test mocks and runtime overrides
        fn = None
        try:
            import app.notifications as notif_pkg
            fn = getattr(notif_pkg, "send_notification", None)
        except ImportError:
            pass
        if not fn:
            try:
                import notifications as notif_pkg
                fn = getattr(notif_pkg, "send_notification", None)
            except ImportError:
                pass
        if not fn:
            fn = send_notification

        if fn:
            try:
                res = fn(
                    event_type=event_type,
                    incident_id=incident_id or f"INC-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
                    target_workload=target_workload,
                    target_pod=target_pod,
                    namespace=namespace,
                    problem=problem or message,
                    action_taken=action_taken,
                    status=status,
                    severity=severity,
                    details=details or message,
                    metrics=metrics,
                    thresholds=thresholds
                )
                res_status = res.get("status", "unknown") if isinstance(res, dict) else "dispatched"
                if res_status in ("smtp_accepted", "sent"):
                    logger.info(f"[ALERT-SENT] Alert for {target_pod} ({event_type}) accepted by SMTP server for {res.get('recipient')}.")
                elif res_status in ("not_configured", "missing_config"):
                    logger.warning(f"[ALERT-NOT-CONFIGURED] Alert for {target_pod} ({event_type}): {res.get('message') or 'SMTP credentials not configured.'}")
                elif res_status == "dry_run":
                    logger.info(f"[ALERT-DRY-RUN] Alert for {target_pod} ({event_type}) simulated (dry-run).")
                elif res_status == "suppressed":
                    logger.debug(f"[ALERT-SUPPRESSED] Duplicate alert for {target_pod}:{event_type} suppressed by cooldown.")
                elif res_status == "timed_out":
                    logger.warning(f"[ALERT-TIMEOUT] SMTP delivery timed out for {target_pod}: {res.get('error') or res.get('message')}")
                elif res_status == "failed":
                    logger.error(f"[ALERT-FAILED] SMTP delivery failed for {target_pod}: {res.get('error') or res.get('message')}")
            except Exception as exc:
                logger.error(f"[ALERT] Notification dispatch error: {exc}")

        if config.ALERT_BACKEND in ("telegram", "both"):
            _send_telegram(message)
        elif not send_notification:
            logger.debug(f"[ALERT-NOOP] {message}")

    if non_blocking:
        import threading
        threading.Thread(target=_do_send, daemon=True).start()
    else:
        _do_send()


def _send_telegram(message: str):
    """Send message to a Telegram chat using the Bot API."""
    try:
        import requests
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("[ALERT] Telegram message sent successfully.")
        else:
            logger.warning(f"[ALERT] Telegram API returned {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"[ALERT] Failed to send Telegram message: {exc}")


# ── Structured event logging ──────────────────────────────────────────────────

def log_event(resource_type: str, resource_name: str, problem: str,
               severity: str, action: str, recovery: str):
    """
    Write a structured event log entry.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator = "─" * 55
    entry = (
        f"\n┌{separator}\n"
        f"│ {now}\n"
        f"│ Resource : {resource_name} ({resource_type})\n"
        f"│ Problem  : {problem}\n"
        f"│ Severity : {severity}\n"
        f"│ Action   : {action}\n"
        f"│ Recovery : {recovery}\n"
        f"└{separator}"
    )
    logger.info(entry)


# ── Pod health evaluation ─────────────────────────────────────────────────────

BAD_POD_REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                   "OOMKilled", "Error", "CreateContainerError"}


def evaluate_pod(pod) -> dict:
    """
    Evaluate a single pod object returned by the Kubernetes API.

    Returns a dict:
      {
        "healthy":  bool,
        "reason":   str,
        "restarts": int,
        "k8s_recovered_oom": bool,
        "k8s_recovered_restart": bool,
        "is_controller": bool,
      }
    """
    pod_name = getattr(pod.metadata, "name", "") or ""
    raw_labels = getattr(pod.metadata, "labels", None) or {}
    labels = raw_labels if isinstance(raw_labels, dict) else {}

    # Self-protection: never mark the monitoring controller as unhealthy for deletion
    is_controller = (labels.get("app") == "health-monitor" and "controller" in pod_name) or pod_name.startswith("health-monitor-controller")

    phase = getattr(pod.status, "phase", None) or "Unknown"
    restarts = 0
    reason = phase  # default reason is the phase itself
    k8s_recovered_oom = False
    k8s_recovered_restart = False

    # Sum all container restarts
    container_statuses = getattr(pod.status, "container_statuses", None) or []
    for cs in container_statuses:
        restarts += getattr(cs, "restart_count", 0) or 0
        
        last_state = getattr(cs, "last_state", None)
        if last_state and getattr(last_state, "terminated", None) and getattr(last_state.terminated, "reason", "") == "OOMKilled":
            state = getattr(cs, "state", None)
            if state and getattr(state, "running", None):
                k8s_recovered_oom = True

        # Track if Kubernetes restarted container and it is currently running and ready
        state = getattr(cs, "state", None)
        is_ready = getattr(cs, "ready", False)
        if (getattr(cs, "restart_count", 0) or 0) > 0 and state and getattr(state, "running", None) and is_ready:
            k8s_recovered_restart = True

        # Check container-level state (waiting.reason is the key signal)
        if state and getattr(state, "waiting", None):
            waiting_reason = getattr(state.waiting, "reason", "") or ""
            if waiting_reason in BAD_POD_REASONS:
                reason = waiting_reason
            if waiting_reason == "CrashLoopBackOff" and last_state and getattr(last_state, "terminated", None) and getattr(last_state.terminated, "reason", "") == "OOMKilled":
                reason = "OOMKilled"

    # A pod is healthy if it is Running and not in a bad waiting state
    healthy = (phase == "Running") and (reason not in BAD_POD_REASONS)

    # Too many restarts also counts as unhealthy unless natively recovered and stable
    if restarts >= config.RESTART_THRESHOLD:
        healthy = False
        if reason == "Running":
            reason = f"ExcessiveRestarts({restarts})"

    # Overrule failure if k8s successfully restarted it for OOM and it is running
    if k8s_recovered_oom and (phase == "Running" and reason not in BAD_POD_REASONS):
        healthy = True
        # Clear out ExcessiveRestarts reason if we're forgiving the restarts
        reason = phase

    return {
        "healthy": healthy,
        "reason": reason,
        "restarts": restarts,
        "k8s_recovered_oom": k8s_recovered_oom,
        "k8s_recovered_restart": k8s_recovered_restart,
        "is_controller": is_controller,
    }


# ── Owner / Deployment resolution ─────────────────────────────────────────────

def get_pod_owner_deployment(pod, apps_v1: client.AppsV1Api) -> str | None:
    """
    Walk the pod's ownerReferences up to find the parent Deployment name.
    Pod → ReplicaSet → Deployment

    Returns the Deployment name, or None if the pod is not managed by a Deployment.
    """
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


# ── Self-healing ──────────────────────────────────────────────────────────────

# Track recovery state per pod  {pod_name: {"attempts": int, "last_attempt": float}}
_recovery_tracker: dict = defaultdict(lambda: {"attempts": 0, "last_attempt": 0.0})


def attempt_recovery(pod, v1: client.CoreV1Api, apps_v1: client.AppsV1Api, reason: str):
    """
    Attempt to heal an unhealthy pod that is managed by a Deployment.

    Steps:
      1. Check retry limit and cooldown.
      2. Identify the parent Deployment.
      3. Delete the bad pod.
      4. Wait for the Deployment to create a replacement.
      5. Verify the replacement belongs to the same Deployment and becomes Ready.
      6. Record success or failure in incident store and event log.
    """
    pod_name = pod.metadata.name
    namespace = pod.metadata.namespace

    # Controller self-protection
    labels = pod.metadata.labels or {}
    if (labels.get("app") == "health-monitor" and "controller" in pod_name) or pod_name.startswith("health-monitor-controller"):
        logger.info(f"[SKIP] {pod_name}: controller pod is protected from self-deletion.")
        return

    tracker = _recovery_tracker[pod_name]

    # ── Cooldown check ────────────────────────────────────────────────────────
    time_since_last = time.time() - tracker["last_attempt"]
    if time_since_last < config.RECOVERY_COOLDOWN:
        wait_remaining = int(config.RECOVERY_COOLDOWN - time_since_last)
        logger.info(f"[COOLDOWN] {pod_name}: waiting {wait_remaining}s before next attempt.")
        return

    # ── Retry limit check ─────────────────────────────────────────────────────
    if tracker["attempts"] >= config.MAX_RECOVERY_ATTEMPTS:
        logger.warning(f"[ABORT] {pod_name}: reached max recovery attempts "
                       f"({config.MAX_RECOVERY_ATTEMPTS}). Manual intervention needed.")
        send_alert(f"🚨 *RECOVERY FAILED* — `{pod_name}`\n"
                   f"Reason: {reason}\n"
                   f"Max attempts ({config.MAX_RECOVERY_ATTEMPTS}) exhausted. "
                   f"Manual intervention required.",
                   event_type="RECOVERY_FAILED",
                   target_workload=labels.get("app", "sample-app"),
                   target_pod=pod_name,
                   problem=reason,
                   action_taken="Recovery aborted — max attempts reached",
                   status="FAILED",
                   severity="CRITICAL")
        log_event("Pod", pod_name, reason, "CRITICAL",
                  "Recovery aborted — max attempts reached", "FAILED")
        if incident_store:
            try:
                incident_store.record_incident(
                    incident_type=f"Recovery Aborted: {reason}",
                    target_pod=pod_name,
                    detection_source="Health Controller",
                    target_workload=labels.get("app", "sample-app"),
                    status="FAILED",
                    recovery_action="Aborted — max recovery attempts reached",
                    recovery_result="FAILED",
                    evidence=f"Exhausted {config.MAX_RECOVERY_ATTEMPTS} attempts.",
                    category="CONTROLLER_ACTION",
                    is_simulated=False
                )
            except Exception:
                pass
        return

    # ── Find parent Deployment ────────────────────────────────────────────────
    deployment_name = get_pod_owner_deployment(pod, apps_v1)
    if not deployment_name:
        logger.warning(f"[SKIP] {pod_name}: not managed by a Deployment. Skipping auto-recovery.")
        return

    # ── Attempt deletion ──────────────────────────────────────────────────────
    tracker["attempts"] += 1
    tracker["last_attempt"] = time.time()

    logger.warning(f"[WARNING] Pod {pod_name} is unhealthy. Reason: {reason}. "
                   f"Owner Deployment: {deployment_name}.")
    logger.info(f"[RECOVERY] Attempt #{tracker['attempts']} — deleting pod {pod_name} "
                f"so Kubernetes Deployment '{deployment_name}' can create a replacement.")

    inc_id = None
    if incident_store:
        try:
            rec = incident_store.record_incident(
                incident_type=f"Controller Healing: {reason}",
                target_pod=pod_name,
                detection_source="Health Controller",
                target_workload=deployment_name,
                status="CONTROLLER_RECOVERING",
                recovery_action=f"Deleted unhealthy pod {pod_name} (attempt #{tracker['attempts']})",
                recovery_result="PENDING_REPLACEMENT",
                evidence=f"Reason: {reason}, Attempt: {tracker['attempts']}/{config.MAX_RECOVERY_ATTEMPTS}",
                category="CONTROLLER_ACTION",
                is_simulated=False
            )
            if rec:
                inc_id = rec.get("incident_id")
        except Exception:
            pass

    # Critical failure alert on detection
    fail_event_type = "CRASHLOOP_BACKOFF" if "CrashLoop" in reason else ("OOM_KILLED" if "OOM" in reason else ("UNHEALTHY_PROBE" if "Probe" in reason or "NotReady" in reason else "CRITICAL_FAILURE"))
    send_alert(f"🚨 *Pod Failure Detected* — `{pod_name}`\n"
               f"Reason: `{reason}`\n"
               f"Workload: `{deployment_name}`\n"
               f"Namespace: `{namespace}`",
               event_type=fail_event_type,
               incident_id=inc_id or "",
               target_workload=deployment_name,
               target_pod=pod_name,
               problem=reason,
               action_taken=f"Initiating auto-recovery attempt #{tracker['attempts']}",
               status="TRIGGERED",
               severity="CRITICAL")

    send_alert(f"⚠️ *Auto-Recovery Started* — `{pod_name}`\n"
               f"Reason: `{reason}`\n"
               f"Attempt #{tracker['attempts']}/{config.MAX_RECOVERY_ATTEMPTS}",
               event_type="RECOVERY_STARTED",
               incident_id=inc_id or "",
               target_workload=deployment_name,
               target_pod=pod_name,
               problem=reason,
               action_taken=f"Deleted pod {pod_name} to trigger replacement",
               status="CONTROLLER_RECOVERING",
               severity="WARNING")

    log_event("Pod", pod_name, reason, "CRITICAL",
              f"Delete unhealthy pod (attempt {tracker['attempts']})", "In Progress")

    try:
        v1.delete_namespaced_pod(pod_name, namespace)
        logger.info(f"[RECOVERY] Pod {pod_name} deleted. Waiting for replacement in Deployment '{deployment_name}'...")
    except ApiException as exc:
        logger.error(f"[RECOVERY] Failed to delete pod {pod_name}: {exc}")
        return

    # ── Wait for replacement pod ──────────────────────────────────────────────
    replacement_pod = _wait_for_replacement(deployment_name, namespace, v1, apps_v1, deleted_pod_name=pod_name)

    if replacement_pod:
        cand_name = replacement_pod if isinstance(replacement_pod, str) else "replacement-pod"
        logger.info(f"[SUCCESS] Replacement pod '{cand_name}' for Deployment '{deployment_name}' verified Running and Ready.")
        send_alert(f"✅ *Recovery Successful* — Deployment `{deployment_name}`\n"
                   f"Deleted pod: `{pod_name}`\nReplacement: `{cand_name}`\nReason was: `{reason}`",
                   event_type="RECOVERY_VERIFIED",
                   incident_id=inc_id or "",
                   target_workload=deployment_name,
                   target_pod=cand_name,
                   problem=reason,
                   action_taken=f"Replacement pod '{cand_name}' verified Running and Ready",
                   status="RECOVERED_VERIFIED",
                   severity="INFO")
        log_event("Pod", pod_name, reason, "CRITICAL",
                  f"Delete unhealthy pod (attempt {tracker['attempts']})", "SUCCESSFUL")
        if incident_store and inc_id:
            try:
                incident_store.update_incident(
                    inc_id,
                    status="RECOVERED_VERIFIED",
                    recovery_action=f"Verified replacement pod '{cand_name}' for Deployment '{deployment_name}' is Ready",
                    recovery_result="SUCCESS",
                    evidence=f"Replacement pod '{cand_name}' confirmed Running & Ready for Deployment '{deployment_name}'.",
                    category="VERIFIED_REPLACEMENT"
                )
            except Exception:
                pass
        # Reset tracker so future failures get fresh attempts
        _recovery_tracker.pop(pod_name, None)
    else:
        logger.error(f"[RECOVERY] Replacement pod for Deployment '{deployment_name}' did not become Ready within "
                     f"{config.RECOVERY_TIMEOUT}s.")
        send_alert(f"❌ *Recovery Timeout* — Deployment `{deployment_name}`\n"
                   f"Replacement pod not Ready after {config.RECOVERY_TIMEOUT}s.",
                   event_type="RECOVERY_FAILED",
                   incident_id=inc_id or "",
                   target_workload=deployment_name,
                   target_pod=pod_name,
                   problem=f"Replacement timed out after {config.RECOVERY_TIMEOUT}s",
                   action_taken=f"Deleted {pod_name}; replacement timed out after {config.RECOVERY_TIMEOUT}s",
                   status="TIMEOUT",
                   severity="CRITICAL")
        log_event("Pod", pod_name, reason, "CRITICAL",
                  f"Delete unhealthy pod (attempt {tracker['attempts']})", "TIMEOUT")
        if incident_store and inc_id:
            try:
                incident_store.update_incident(
                    inc_id,
                    status="TIMEOUT",
                    recovery_action=f"Deleted {pod_name}; replacement pod timed out after {config.RECOVERY_TIMEOUT}s",
                    recovery_result="TIMEOUT",
                    evidence=f"Replacement pod did not become Ready within {config.RECOVERY_TIMEOUT}s",
                    category="CONTROLLER_ACTION"
                )
            except Exception:
                pass


def _wait_for_replacement(deployment_name: str, namespace: str,
                           v1: client.CoreV1Api, apps_v1: client.AppsV1Api = None,
                           deleted_pod_name: str = None) -> str | None:
    """
    Poll pods specifically belonging to deployment_name until a healthy replacement
    is Running+Ready (excluding the deleted pod and non-deployment pods),
    or until RECOVERY_TIMEOUT seconds have elapsed.

    Returns the verified replacement pod name (truthy string) on success, or None on failure.
    Never marks recovery successful merely because some unrelated pod is Running.
    """
    deadline = time.time() + config.RECOVERY_TIMEOUT
    logger.info(f"[RECOVERY] Waiting up to {config.RECOVERY_TIMEOUT}s for Ready replacement in Deployment '{deployment_name}' "
                f"in namespace '{namespace}'...")

    while time.time() < deadline:
        time.sleep(min(2, config.RECOVERY_TIMEOUT))
        try:
            pods = v1.list_namespaced_pod(namespace).items
            for candidate in pods:
                cand_name = candidate.metadata.name or ""
                # Exclude the pod that was just deleted
                if deleted_pod_name and cand_name == deleted_pod_name:
                    continue
                # Exclude terminating pods
                if getattr(candidate.metadata, "deletion_timestamp", None) is not None:
                    continue

                # Verify ownership: candidate must belong to deployment_name
                cand_owner = None
                if apps_v1:
                    cand_owner = get_pod_owner_deployment(candidate, apps_v1)
                elif candidate.metadata.labels:
                    cand_owner = candidate.metadata.labels.get("app")

                if cand_owner != deployment_name:
                    continue

                if candidate.status.phase == "Running":
                    statuses = candidate.status.container_statuses or []
                    if statuses and all(cs.ready for cs in statuses):
                        logger.info(f"[RECOVERY] Replacement pod '{cand_name}' for Deployment '{deployment_name}' is Ready.")
                        return cand_name
        except ApiException:
            pass

    return None


# ── Node health evaluation ─────────────────────────────────────────────────────

def check_nodes(v1: client.CoreV1Api):
    """
    Check all nodes for NotReady, MemoryPressure, DiskPressure, PIDPressure.
    Note: In a single-node cluster like local Minikube, node failover cannot
    reschedule pods to other nodes automatically without multi-node topology.
    """
    try:
        nodes = v1.list_node().items
    except ApiException as exc:
        logger.error(f"[ERROR] Cannot list nodes: {exc}")
        return

    for node in nodes:
        node_name = node.metadata.name
        conditions = {c.type: c.status for c in (node.status.conditions or [])}

        if conditions.get("Ready") != "True":
            logger.warning(f"[NODE-WARNING] Node '{node_name}' is NOT READY. "
                           f"(Limitation: In single-node Minikube, automated node failover is unsupported).")
            send_alert(f"🔴 *Node NotReady* — `{node_name}`\n"
                       f"⚠️ Single-node cluster limitation: Automated node migration unavailable.",
                       event_type="NODE_NOT_READY",
                       incident_id=f"INC-NODE-{node_name}-{int(time.time())}",
                       target_workload="node",
                       target_pod=node_name,
                       problem="Node NotReady condition",
                       action_taken="Alert sent; automated failover requires multi-node cluster",
                       status="NOT_READY",
                       severity="CRITICAL")
            log_event("Node", node_name, "NotReady", "CRITICAL",
                      "Alert sent", "N/A — requires multi-node cluster or host intervention")

        for pressure in ("MemoryPressure", "DiskPressure", "PIDPressure"):
            if conditions.get(pressure) == "True":
                logger.warning(f"[NODE-WARNING] Node '{node_name}' has {pressure}.")
                send_alert(f"⚠️ *{pressure}* detected on node `{node_name}`",
                           event_type=f"NODE_{pressure.upper()}",
                           incident_id=f"INC-PRESSURE-{node_name}-{int(time.time())}",
                           target_workload="node",
                           target_pod=node_name,
                           problem=pressure,
                           action_taken="Resource pressure telemetry dispatched",
                           status="PRESSURE_DETECTED",
                           severity="WARNING")
                log_event("Node", node_name, pressure, "WARNING", "Alert sent", "N/A")


# ── Main monitoring loop ──────────────────────────────────────────────────────

def run():
    connect_to_cluster()
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()

    logger.info("=" * 60)
    logger.info(" Auto Kubernetes Health Monitor — STARTED")
    logger.info(f" Namespace     : {config.WATCH_NAMESPACE}")
    logger.info(f" Poll interval : {config.POLL_INTERVAL}s")
    logger.info(f" Alert backend : {config.ALERT_BACKEND}")
    logger.info("=" * 60)

    while True:
        logger.info(f"[POLL] Starting health check at "
                    f"{datetime.datetime.now().strftime('%H:%M:%S')} ...")

        # ── 1. Check Nodes ────────────────────────────────────────────────────
        check_nodes(v1)

        # ── 2. Check Pods ─────────────────────────────────────────────────────
        try:
            pods = v1.list_namespaced_pod(config.WATCH_NAMESPACE).items
        except ApiException as exc:
            logger.error(f"[ERROR] Cannot list pods in '{config.WATCH_NAMESPACE}': {exc}")
            time.sleep(config.POLL_INTERVAL)
            continue

        healthy_count = 0
        unhealthy_count = 0

        for pod in pods:
            pod_name = pod.metadata.name
            phase = pod.status.phase or "Unknown"
            result = evaluate_pod(pod)

            if result.get("is_controller"):
                healthy_count += 1
                logger.info(f"[INFO] Pod {pod_name} is controller itself. Protected from self-deletion.")
                continue

            if result["healthy"]:
                healthy_count += 1
                logger.info(f"[INFO] Pod {pod_name} is healthy (phase={phase}, "
                            f"restarts={result['restarts']})")
                if result.get("k8s_recovered_oom"):
                    logger.info(f"[K8S-NATIVE-RECOVERY] Kubernetes handled OOM recovery for {pod_name} natively. Custom controller holding back deletion.")
                    if incident_store:
                        try:
                            incident_store.record_incident(
                                incident_type="Native Kubernetes Recovery (OOMKilled)",
                                target_pod=pod_name,
                                detection_source="Kubelet (Native Engine)",
                                target_workload=pod.metadata.labels.get("app", "sample-app") if pod.metadata.labels else "sample-app",
                                status="NATIVE_RECOVERED",
                                recovery_action="Kubernetes native container restart; controller skipped redundant deletion",
                                recovery_result="SUCCESS",
                                evidence=f"Pod restarted natively and is currently Running & Ready. Restarts={result['restarts']}",
                                category="NATIVE_K8S",
                                is_simulated=False
                            )
                        except Exception:
                            pass
                elif result.get("k8s_recovered_restart"):
                    logger.info(f"[K8S-NATIVE-RECOVERY] Kubernetes handled container restart for {pod_name} natively (restarts={result['restarts']}). Pod is Ready.")
                    if incident_store:
                        try:
                            incident_store.record_incident(
                                incident_type="Native Kubernetes Container Restart",
                                target_pod=pod_name,
                                detection_source="Kubelet (Native Engine)",
                                target_workload=pod.metadata.labels.get("app", "sample-app") if pod.metadata.labels else "sample-app",
                                status="NATIVE_RECOVERED",
                                recovery_action="Kubernetes native container restart; controller verified pod is Ready",
                                recovery_result="SUCCESS",
                                evidence=f"Container restart count={result['restarts']}, state=Running, ready=True.",
                                category="NATIVE_K8S",
                                is_simulated=False
                            )
                        except Exception:
                            pass
            else:
                unhealthy_count += 1
                reason = result["reason"]
                logger.warning(f"[WARNING] Pod {pod_name} is UNHEALTHY "
                               f"(reason={reason}, restarts={result['restarts']})")
                attempt_recovery(pod, v1, apps_v1, reason)

        logger.info(f"[SUMMARY] Pods — Healthy: {healthy_count}, "
                    f"Unhealthy: {unhealthy_count}, Total: {len(pods)}")

        # ── 3. Sleep until next poll ──────────────────────────────────────────
        logger.info(f"[POLL] Next check in {config.POLL_INTERVAL}s.\n")
        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    run()
