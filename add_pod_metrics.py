import json

with open("dashboards/grafana-dashboard.json", "r") as f:
    dashboard = json.load(f)

# Find the Resource Usage Over Time row (id 101)
# The panels following it are CPU Usage (id 5) and Memory Usage (id 6)
# We will change their targets to include pod-level metrics.

for panel in dashboard['panels']:
    if panel.get('id') == 5:
        panel['title'] = "CPU Usage (Node vs Pods)"
        panel['targets'].append({
            "expr": "sum(rate(container_cpu_usage_seconds_total{namespace=\"health-monitor\", container=\"sample-app-ui\"}[2m])) by (pod)",
            "legendFormat": "{{ pod }} CPU"
        })
    elif panel.get('id') == 6:
        panel['title'] = "Memory Usage (Node vs Pods)"
        panel['targets'].append({
            "expr": "sum(container_memory_working_set_bytes{namespace=\"health-monitor\", container=\"sample-app-ui\"}) by (pod)",
            "legendFormat": "{{ pod }} Mem"
        })

with open("dashboards/grafana-dashboard.json", "w") as f:
    json.dump(dashboard, f, indent=2)

print("Dashboard updated!")
