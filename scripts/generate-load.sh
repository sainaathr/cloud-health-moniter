#!/usr/bin/env bash
# =============================================================================
# generate-load.sh — Generate HTTP load to trigger HPA scaling
#
# Continuously sends HTTP requests to the sample-app to push CPU above
# 60% so that the HPA kicks in and adds more replicas.
#
# Usage:
#   chmod +x scripts/generate-load.sh
#   ./scripts/generate-load.sh
#   (Press Ctrl+C to stop)
# =============================================================================

NAMESPACE="health-monitor"

echo "▶ Getting Minikube IP..."
MINIKUBE_IP=$(minikube ip)
APP_URL="http://${MINIKUBE_IP}:30080"

echo "▶ Target URL: $APP_URL"
echo "▶ Generating load... (Ctrl+C to stop)"
echo ""

# Show HPA in a background loop
(while true; do
  echo "── HPA Status ──────────────────────────────────────────"
  kubectl get hpa -n $NAMESPACE 2>/dev/null
  kubectl get pods -n $NAMESPACE 2>/dev/null
  sleep 20
done) &

BG_PID=$!

# Send continuous HTTP requests
while true; do
  # Use curl (-s = silent, -o = discard body, -m 2 = 2s timeout)
  curl -s -o /dev/null -m 2 "$APP_URL" &
done

# Cleanup background monitor on exit
trap "kill $BG_PID 2>/dev/null" EXIT
