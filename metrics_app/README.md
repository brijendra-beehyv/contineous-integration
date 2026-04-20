# k8s-metrics

Scrapes Kubernetes node/pod metrics via `kubectl top` and exposes them as a Prometheus HTTP endpoint.

## Requirements

- `kubectl` configured and pointing to your cluster (`kubectl top nodes` must work)
- `metrics-server` installed in the cluster
- Python 3.10+

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
# defaults: port=8000, scrape interval=30s
python metrics_collector.py

# custom port and interval
METRICS_PORT=9100 SCRAPE_INTERVAL=15 python metrics_collector.py
```

## Prometheus Config

Add this to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: k8s-kubectl-metrics
    static_configs:
      - targets: ['<this-host>:8000']
```

## Metrics Exposed

| Metric | Labels | Description |
|---|---|---|
| `k8s_node_cpu_cores` | `node` | Node CPU usage in cores |
| `k8s_node_memory_bytes` | `node` | Node memory usage in bytes |
| `k8s_pod_cpu_cores` | `namespace`, `pod` | Pod CPU usage in cores |
| `k8s_pod_memory_bytes` | `namespace`, `pod` | Pod memory usage in bytes |

*Version*: 7