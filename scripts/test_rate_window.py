import requests

q_rate = 'sum(rate(http_requests_total{service="sample-app-service"}[5m])) by (endpoint)'
res_rate = requests.get('http://127.0.0.1:9090/api/v1/query', params={'query': q_rate}).json()
print("Rate [5m]:", [(r['metric'], r['value'][1]) for r in res_rate.get('data', {}).get('result', []) if float(r['value'][1]) > 0])

q_lat = '(sum(rate(http_request_duration_seconds_sum{service="sample-app-service"}[5m])) by (endpoint) / sum(rate(http_request_duration_seconds_count{service="sample-app-service"}[5m])) by (endpoint))'
res_lat = requests.get('http://127.0.0.1:9090/api/v1/query', params={'query': q_lat}).json()
print("Latency [5m]:", [(r['metric'], r['value'][1]) for r in res_lat.get('data', {}).get('result', []) if str(r['value'][1]) != 'NaN'])
