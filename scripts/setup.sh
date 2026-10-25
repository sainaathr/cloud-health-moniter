#!/usr/bin/env bash
# =============================================================================
# setup.sh — One-shot setup script for the Auto K8s Health Monitor project
#
# What this does:
#   1. Starts Minikube
#   2. Enables necessary addons (metrics-server)
#   3. Creates the project namespace
#   4. Deploys the sample application
#   5. Sets up RBAC
#   6. Installs Prometheus + Grafana via Helm
#
# Usage:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh
# =============================================================================
set -euo pipefail

echo "╔══════════════════════════════════════════════════════╗"
echo "║   Auto Kubernetes Health Monitor — Setup Script     ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── Step 1: Start Minikube ────────────────────────────────────────────────────
echo ""
echo "▶ Step 1: Starting Minikube..."
minikube start --driver=docker --cpus=2 --memory=4096

echo "▶ Enabling metrics-server addon (required for HPA)..."
minikube addons enable metrics-server

echo "▶ Verify node is Ready..."
kubectl wait --for=condition=Ready node/minikube --timeout=120s

# ── Step 2: Namespace ─────────────────────────────────────────────────────────
echo ""
echo "▶ Step 2: Creating namespace..."
kubectl apply -f k8s/namespace.yaml

# ── Step 3: RBAC ─────────────────────────────────────────────────────────────
echo ""
echo "▶ Step 3: Applying RBAC (ServiceAccount, ClusterRole, Binding)..."
kubectl apply -f k8s/service-account.yaml
kubectl apply -f k8s/role.yaml
kubectl apply -f k8s/role-binding.yaml

# ── Step 4: Sample Application ────────────────────────────────────────────────
echo ""
echo "▶ Step 4: Deploying sample application (nginx)..."
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml

echo "▶ Waiting for sample-app pods to be Ready..."
kubectl wait --for=condition=Ready pod -l app=sample-app \
  -n health-monitor --timeout=120s

# ── Step 5: Prometheus & Grafana ──────────────────────────────────────────────
echo ""
echo "▶ Step 5: Installing Prometheus + Grafana via Helm..."

# Add the Helm repository if not already added
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Install the kube-prometheus-stack (includes Prometheus, Grafana, Alertmanager,
# kube-state-metrics, and node-exporter)
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminPassword=admin123 \
  --set prometheus.prometheusSpec.retention=24h \
  --wait \
  --timeout=10m

echo ""
echo "▶ Verifying monitoring pods..."
kubectl get pods -n monitoring

# ── Step 6: Apply Prometheus Rules ────────────────────────────────────────────
echo ""
echo "▶ Step 6: Applying custom Prometheus alerting rules..."
kubectl apply -f monitoring/prometheus-rules.yaml -n monitoring

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✅  Setup Complete!                               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n health-monitor"
echo "  kubectl get pods -n monitoring"
echo "  minikube service sample-app-service -n health-monitor"
echo ""
echo "Access Grafana:"
echo "  kubectl port-forward svc/monitoring-grafana 3000:80 -n monitoring"
echo "  Open: http://localhost:3000  (admin / admin123)"
echo ""
echo "Access Prometheus:"
echo "  kubectl port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090 -n monitoring"
echo "  Open: http://localhost:9090"
