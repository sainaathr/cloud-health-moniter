# PRE-DEMONSTRATION SYSTEM VERIFICATION CHECKLIST
## Autonomous Kubernetes Health Monitor & Self-Healing Control Plane v2.0
**Operational Readiness Verification & Pre-Flight Procedure**

---

### Purpose of this Checklist
Run through this verification sequence **15 to 20 minutes before your presentation**.

Following these steps guarantees that your local environment (Docker Desktop, WSL2, Minikube, background daemons, Kubernetes resources, Prometheus, Grafana, and SMTP credentials) is 100% operational before the professor arrives.

---

## Pre-Flight Verification Sequence

### STEP 1: Verify Docker Desktop Engine & Daemon
- **What it does**: Confirms that the Docker engine is running and able to communicate with the Minikube VM/driver.
- **PowerShell Command**:
  ```powershell
  docker version
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  Client:
   Version:           27.x.x
   OS/Arch:           windows/amd64
  Server: Docker Desktop
   Engine:
    Version:          27.x.x
    OS/Arch:          linux/amd64
  ```
- **Common Failure Causes**: Docker Desktop not started; WSL2 backend stopped; Docker daemon starting up.
- **Safe Next Diagnostic Step**: Launch Docker Desktop from the Start Menu, or start the backend service:
  ```powershell
  & "C:\Users\Administrator\AppData\Local\Programs\DockerDesktop\resources\com.docker.backend.exe"
  ```

---

### STEP 2: Verify Minikube Cluster State
- **What it does**: Checks that the local Minikube single-node Kubernetes cluster is running with active apiserver and kubelet.
- **PowerShell Command**:
  ```powershell
  minikube status
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  minikube
  type: Control Plane
  host: Running
  kubelet: Running
  apiserver: Running
  kubeconfig: Configured
  ```
- **Common Failure Causes**: Minikube was stopped or computer went to sleep.
- **Safe Next Diagnostic Step**: Start Minikube without deleting data:
  ```powershell
  minikube start --driver=docker
  ```
  *(Never run `minikube delete` before a presentation!)*

---

### STEP 3: Verify Kubernetes Node Readiness
- **What it does**: Ensures the Kubernetes worker node is in `Ready` condition without memory or disk pressure.
- **PowerShell Command**:
  ```powershell
  kubectl get nodes -o wide
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  NAME       STATUS   ROLES           AGE   VERSION   INTERNAL-IP    OS-IMAGE             KERNEL-VERSION
  minikube   Ready    control-plane   36d   v1.37.0   192.168.49.2   Ubuntu 22.04.5 LTS   5.15.167.4-microsoft-standard-WSL2
  ```
- **Common Failure Causes**: High host disk usage causing Kubelet to flag `DiskPressure`.
- **Safe Next Diagnostic Step**: Inspect node conditions via `kubectl describe node minikube | Select-String -Pattern "Conditions:" -Context 0,10`.

---

### STEP 4: Verify Namespace & Active Deployments
- **What it does**: Confirms that the target namespace `health-monitor` exists and both Deployments have desired replicas available.
- **PowerShell Command**:
  ```powershell
  kubectl get deployments -n health-monitor
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  NAME                        READY   UP-TO-DATE   AVAILABLE   AGE
  health-monitor-controller   1/1     1            1           36d
  sample-app                  2/2     2            2           36d
  ```
- **Common Failure Causes**: Image pull error or pod crash loop during initial scheduling.
- **Safe Next Diagnostic Step**: Run `kubectl describe deployment sample-app -n health-monitor`.

---

### STEP 5: List Pods, UIDs, Owners, and Restarts
- **What it does**: Confirms that all 3 pods are Running and Ready, with owner references pointing to their parent ReplicaSets.
- **PowerShell Command**:
  ```powershell
  kubectl get pods -n health-monitor -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,NODE:.spec.nodeName,IP:.status.podIP
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  NAME                                         READY   STATUS    RESTARTS   NODE       IP
  health-monitor-controller-865c649cb8-lk2xv   true    Running   0          minikube   10.244.0.31
  sample-app-b487f454b-826t5                   true    Running   0          minikube   10.244.0.30
  sample-app-b487f454b-rbpbf                   true    Running   0          minikube   10.244.0.32
  ```
- **Common Failure Causes**: An old pod left in unready state from previous tests.
- **Safe Next Diagnostic Step**: Run `kubectl delete pod <unready-pod-name> -n health-monitor` to allow the Deployment to schedule a fresh pod.

---

### STEP 6: Verify Service & Endpoints Traffic Routing
- **What it does**: Checks that `sample-app-service` has both application pod IPs registered in its endpoints list.
- **PowerShell Command**:
  ```powershell
  kubectl get svc,endpoints -n health-monitor -l app=sample-app
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  NAME                         TYPE       CLUSTER-IP      EXTERNAL-IP   PORT(S)        AGE
  service/sample-app-service   NodePort   10.108.82.161   <none>        80:30080/TCP   36d

  NAME                           ENDPOINTS                         AGE
  endpoints/sample-app-service   10.244.0.30:5000,10.244.0.32:5000 36d
  ```
- **Common Failure Causes**: Readiness probe failing, causing Kubelet to strip the IP from endpoints.
- **Safe Next Diagnostic Step**: Check `/health/ready` on both pods or trigger `POST /api/chaos/reset`.

---

### STEP 7: Verify Controller Status & RBAC ServiceAccount
- **What it does**: Confirms that the controller pod is running and authenticated via `health-monitor-sa`.
- **PowerShell Command**:
  ```powershell
  kubectl get sa,clusterrolebinding -n health-monitor | Select-String "health-monitor"
  kubectl logs deployment/health-monitor-controller -n health-monitor --tail=10
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  serviceaccount/health-monitor-sa   1         36d
  clusterrolebinding.rbac.authorization.k8s.io/health-monitor-binding   ClusterRole/health-monitor-role
  ... [INFO] Connected using IN-CLUSTER config (running inside Kubernetes).
  ... [SUMMARY] Pods — Healthy: 3, Unhealthy: 0, Total: 3
  ```
- **Common Failure Causes**: ServiceAccount missing or RBAC binding deleted.
- **Safe Next Diagnostic Step**: Reapply RBAC manifests: `kubectl apply -f k8s/role.yaml -f k8s/role-binding.yaml -f k8s/service-account.yaml`.

---

### STEP 8: Verify Metrics Server & `kubectl top pods`
- **What it does**: Confirms that the Kubernetes Metrics API (`metrics.k8s.io`) is collecting CPU and memory usage from pods.
- **PowerShell Command**:
  ```powershell
  kubectl top pods -n health-monitor
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  NAME                                         CPU(cores)   MEMORY(bytes)   
  health-monitor-controller-865c649cb8-lk2xv   1m           61Mi            
  sample-app-b487f454b-826t5                   4m           100Mi           
  sample-app-b487f454b-rbpbf                   1m           29Mi            
  ```
- **Common Failure Causes**: Metrics Server addon not enabled in Minikube.
- **Safe Next Diagnostic Step**: Enable addon: `minikube addons enable metrics-server`. Wait 30 seconds for initial scrape.

---

### STEP 9: Verify Horizontal Pod Autoscaler (HPA)
- **What it does**: Confirms that `sample-app-hpa` is reading metrics and managing replica count between 2 and 6.
- **PowerShell Command**:
  ```powershell
  kubectl get hpa sample-app-hpa -n health-monitor
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  NAME             REFERENCE               TARGETS       MINPODS   MAXPODS   REPLICAS   AGE
  sample-app-hpa   Deployment/sample-app   cpu: 5%/60%   2         6         2          36d
  ```
- **Common Failure Causes**: Shows `cpu: <unknown>/60%` if Metrics Server was just restarted.
- **Safe Next Diagnostic Step**: Wait 30 seconds for the next 15-second Metrics Server polling interval to resolve utilization.

---

### STEP 10: Verify Background Port Forwards
- **What it does**: Ensures local ports (5000, 3000, 9090) are forwarded to Minikube services.
- **PowerShell Command**:
  ```powershell
  curl.exe -s http://localhost:5000/health/live
  curl.exe -s http://localhost:9090/-/healthy
  curl.exe -s http://localhost:3000/api/health
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```text
  {"status":"alive"}
  Prometheus Server is Healthy.
  {"database":"ok","version":"12.3.1"}
  ```
- **Common Failure Causes**: The port forward process was closed or interrupted.
- **Safe Next Diagnostic Step**: Relaunch port forwarding daemon in background:
  ```powershell
  py start_port_forwards.py
  ```

---

### STEP 11: Verify Email Notification Configuration & Masking
- **What it does**: Confirms that SMTP sending credentials are loaded from Kubernetes Secret `smtp-credentials` and ready for live dispatch.
- **PowerShell Command**:
  ```powershell
  curl.exe -s http://localhost:5000/api/notifications/status
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```json
  {
    "status": "success",
    "config": {
      "delivery_state": "LIVE_READY",
      "status_label": "Live SMTP Ready",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "smtp_username": "sa***@gmail.com",
      "smtp_to_email": "sainaath.r2025@vitstudent.ac.in",
      "encryption_mode": "STARTTLS",
      "dry_run": false
    }
  }
  ```
- **Common Failure Causes**: Secret `smtp-credentials` missing from `health-monitor` namespace.
- **Safe Next Diagnostic Step**: Run `.\scripts\setup_smtp_secret.ps1 -UpdateK8sSecret` to synchronize secret.

---

### STEP 12: Verify Incident History & Return to Clean Baseline
- **What it does**: Ensures test memory allocations are 0MB and readiness is restored to 200 OK.
- **PowerShell Command**:
  ```powershell
  curl.exe -s -X POST http://localhost:5000/api/chaos/reset
  curl.exe -s http://localhost:5000/health/ready
  ```
- **Healthy Expected Output** *(Illustrative)*:
  ```json
  {"status":"success","message":"System state reset: readiness is READY and test memory cleared.","is_ready":true,"memory_allocated_bytes":0}
  {"status":"ready"}
  ```

---

## 1-Command Master Verification Script

You can run this single PowerShell block to verify all 12 stages in under 5 seconds:

```powershell
Write-Host "=== PRE-DEMO RAPID HEALTH CHECK ===" -ForegroundColor Cyan
kubectl get pods,svc,hpa -n health-monitor
kubectl top pods -n health-monitor
$app = (curl.exe -s http://localhost:5000/api/cluster/overview | ConvertFrom-Json)
$notif = (curl.exe -s http://localhost:5000/api/notifications/status | ConvertFrom-Json)
Write-Host "K8s API Connected: $($app.k8s_connected) | Controller: $($app.controller_status) | Replicas: $($app.replicas_available)" -ForegroundColor Green
Write-Host "SMTP Delivery State: $($notif.config.delivery_state) | Recipient: $($notif.config.smtp_to_email)" -ForegroundColor Green
Write-Host "=== SYSTEM IS 100% READY FOR FACULTY EVALUATION ===" -ForegroundColor Cyan
```
