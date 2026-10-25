#!/usr/bin/env bash
# =============================================================================
# test-failure.sh — Failure simulation scripts for demo purposes
#
# Tests:
#   1. Pod deletion (tests self-healing)
#   2. CrashLoopBackOff simulation (bad command)
#   3. High CPU load (tests HPA)
#   4. Manual recovery demo
#
# Usage:
#   chmod +x scripts/test-failure.sh
#   ./scripts/test-failure.sh <test-number>
#   Example: ./scripts/test-failure.sh 1
# =============================================================================

NAMESPACE="health-monitor"

show_state() {
  echo ""
  echo "── Current State ──────────────────────────────────────"
  kubectl get pods -n $NAMESPACE
  echo "────────────────────────────────────────────────────────"
}

# ─────────────────────────────────────────────
# TEST 1: Force-delete a pod
# Kubernetes Deployment controller will recreate it automatically
# ─────────────────────────────────────────────
test_pod_deletion() {
  echo ""
  echo "═══ TEST 1: Pod Deletion & Self-Healing ═══"
  echo "INITIAL STATE:"
  show_state

  # Pick the first sample-app pod
  POD=$(kubectl get pod -n $NAMESPACE -l app=sample-app \
        -o jsonpath="{.items[0].metadata.name}")
  echo ""
  echo "▶ Deleting pod: $POD"
  kubectl delete pod $POD -n $NAMESPACE

  echo "▶ Watching Kubernetes recreate the pod..."
  sleep 5
  echo "STATE AFTER DELETION:"
  show_state

  echo "▶ Waiting for new pod to be Ready..."
  kubectl wait --for=condition=Ready pod -l app=sample-app \
    -n $NAMESPACE --timeout=120s

  echo "FINAL STATE:"
  show_state
  echo "✅ Test 1 PASSED: Pod was deleted and automatically recreated."
}

# ─────────────────────────────────────────────
# TEST 2: Deploy a broken pod (CrashLoopBackOff)
# Uses a bad command so the container immediately exits
# ─────────────────────────────────────────────
test_crash_loop() {
  echo ""
  echo "═══ TEST 2: CrashLoopBackOff Simulation ═══"
  echo "▶ Deploying a broken pod..."

  kubectl run crash-test \
    --image=busybox \
    --restart=Always \
    -n $NAMESPACE \
    -- /bin/sh -c "echo 'Starting...'; sleep 2; exit 1"

  echo "▶ Waiting 30s for crash loop to develop..."
  sleep 30
  echo "STATE (should show CrashLoopBackOff or Error):"
  show_state
  kubectl describe pod crash-test -n $NAMESPACE | grep -A5 "State:"

  echo ""
  echo "▶ Cleaning up crash-test pod..."
  kubectl delete pod crash-test -n $NAMESPACE --ignore-not-found
  echo "✅ Test 2 DONE: CrashLoopBackOff was simulated."
}

# ─────────────────────────────────────────────
# TEST 3: High CPU load (triggers HPA)
# ─────────────────────────────────────────────
test_cpu_load() {
  echo ""
  echo "═══ TEST 3: High CPU Load (HPA Demo) ═══"
  echo "▶ Running CPU stress inside cluster..."

  # Run a CPU burner pod
  kubectl run cpu-stress \
    --image=containerstack/cpustress \
    --restart=Never \
    -n $NAMESPACE \
    -- --cpu 4 --timeout 120s

  echo "▶ Monitoring HPA (check every 15s for 2 minutes)..."
  for i in {1..8}; do
    sleep 15
    echo "── Iteration $i ──"
    kubectl get hpa -n $NAMESPACE
    kubectl get pods -n $NAMESPACE
  done

  kubectl delete pod cpu-stress -n $NAMESPACE --ignore-not-found
  echo "✅ Test 3 DONE: CPU load applied. Check HPA scaled replicas."
}

# ─────────────────────────────────────────────
# TEST 4: Manual recovery demo
# Shows the before/after of deleting multiple pods
# ─────────────────────────────────────────────
test_recovery_demo() {
  echo ""
  echo "═══ TEST 4: Recovery Demo ═══"
  echo "INITIAL STATE:"
  show_state

  echo "▶ Deleting all sample-app pods simultaneously..."
  kubectl delete pods -l app=sample-app -n $NAMESPACE

  echo "▶ Kubernetes Deployment controller will recreate them..."
  sleep 5
  echo "STATE IMMEDIATELY AFTER:"
  show_state

  echo "▶ Waiting for full recovery..."
  kubectl wait --for=condition=Ready pod -l app=sample-app \
    -n $NAMESPACE --timeout=180s

  echo "FINAL STATE:"
  show_state
  echo "✅ Test 4 DONE: All pods recovered."
}

# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
case "${1:-help}" in
  1) test_pod_deletion ;;
  2) test_crash_loop ;;
  3) test_cpu_load ;;
  4) test_recovery_demo ;;
  *)
    echo "Usage: $0 <test-number>"
    echo "  1 — Pod deletion & self-healing"
    echo "  2 — CrashLoopBackOff simulation"
    echo "  3 — High CPU load (HPA demo)"
    echo "  4 — Full recovery demo"
    ;;
esac
