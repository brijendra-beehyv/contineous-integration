import os
import time
import logging
from prometheus_client import start_http_server, Gauge, REGISTRY, PROCESS_COLLECTOR, PLATFORM_COLLECTOR
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

for collector in [PROCESS_COLLECTOR, PLATFORM_COLLECTOR]:
    try:
        REGISTRY.unregister(collector)
    except Exception:
        pass

NODE_CPU_CORES = Gauge("k8s_node_cpu_cores", "Node CPU usage in cores", ["node"])
NODE_MEM_BYTES = Gauge("k8s_node_memory_bytes", "Node memory usage in bytes", ["node"])
POD_CPU_CORES = Gauge("k8s_pod_cpu_cores", "Pod CPU usage in cores", ["namespace", "pod"])
POD_MEM_BYTES = Gauge("k8s_pod_memory_bytes", "Pod memory usage in bytes", ["namespace", "pod"])

NODE_CONDITION = Gauge("k8s_node_condition", "Node condition status", ["node", "condition"])
NODE_ALLOCATABLE_CPU = Gauge("k8s_node_allocatable_cpu", "Allocatable CPU cores", ["node"])
NODE_ALLOCATABLE_MEM = Gauge("k8s_node_allocatable_memory_bytes", "Allocatable memory", ["node"])

POD_PHASE = Gauge("k8s_pod_phase", "Pod phase (1=active)", ["namespace", "pod", "phase"])
POD_CONTAINER_CPU = Gauge("k8s_pod_container_cpu_cores", "Container CPU usage", ["namespace", "pod", "container"])
POD_CONTAINER_MEM = Gauge("k8s_pod_container_memory_bytes", "Container memory usage", ["namespace", "pod", "container"])

POD_REQUEST_CPU = Gauge("k8s_pod_request_cpu_cores", "Requested CPU", ["namespace", "pod"])
POD_REQUEST_MEM = Gauge("k8s_pod_request_memory_bytes", "Requested memory", ["namespace", "pod"])

POD_LIMIT_CPU = Gauge("k8s_pod_limit_cpu_cores", "CPU limit", ["namespace", "pod"])
POD_LIMIT_MEM = Gauge("k8s_pod_limit_memory_bytes", "Memory limit", ["namespace", "pod"])


def parse_cpu(value: str) -> float:
    try:
        if value.endswith("n"):
            return int(value[:-1]) / 1_000_000_000
        if value.endswith("u"):
            return int(value[:-1]) / 1_000_000
        if value.endswith("m"):
            return int(value[:-1]) / 1000
        return float(value)
    except Exception:
        log.error("Unknown CPU format: %s", value)
        raise


def parse_memory(value: str) -> float:
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "k": 1000, "M": 1000**2, "G": 1000**3}
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier
    return float(value)


def collect():
    custom = client.CustomObjectsApi()

    node_metrics = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")
    for item in node_metrics["items"]:
        node = item["metadata"]["name"]
        NODE_CPU_CORES.labels(node=node).set(parse_cpu(item["usage"]["cpu"]))
        NODE_MEM_BYTES.labels(node=node).set(parse_memory(item["usage"]["memory"]))
    log.info("Collected metrics for %d nodes", len(node_metrics["items"]))

    pod_metrics = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
    for item in pod_metrics["items"]:
        ns = item["metadata"]["namespace"]
        pod = item["metadata"]["name"]
        cpu = sum(parse_cpu(c["usage"]["cpu"]) for c in item["containers"])
        mem = sum(parse_memory(c["usage"]["memory"]) for c in item["containers"])
        POD_CPU_CORES.labels(namespace=ns, pod=pod).set(cpu)
        POD_MEM_BYTES.labels(namespace=ns, pod=pod).set(mem)
    log.info("Collected metrics for %d pods", len(pod_metrics["items"]))

    core = client.CoreV1Api()
    nodes = core.list_node()

    for node in nodes.items:
        name = node.metadata.name

        # Allocatable
        alloc = node.status.allocatable
        if "cpu" in alloc:
            NODE_ALLOCATABLE_CPU.labels(node=name).set(parse_cpu(alloc["cpu"]))
        if "memory" in alloc:
            NODE_ALLOCATABLE_MEM.labels(node=name).set(parse_memory(alloc["memory"]))

        # Conditions
        for cond in node.status.conditions:
            value = 1 if cond.status == "True" else 0
            NODE_CONDITION.labels(node=name, condition=cond.type).set(value)

    pods = core.list_pod_for_all_namespaces()

    for pod in pods.items:
        ns = pod.metadata.namespace
        name = pod.metadata.name

        # Pod phase
        for phase in ["Pending", "Running", "Succeeded", "Failed", "Unknown"]:
            POD_PHASE.labels(namespace=ns, pod=name, phase=phase).set(
                1 if pod.status.phase == phase else 0
            )

        # Requests & Limits
        total_req_cpu = total_req_mem = 0
        total_lim_cpu = total_lim_mem = 0

        for c in pod.spec.containers:
            resources = c.resources

            if resources.requests:
                if "cpu" in resources.requests:
                    total_req_cpu += parse_cpu(resources.requests["cpu"])
                if "memory" in resources.requests:
                    total_req_mem += parse_memory(resources.requests["memory"])

            if resources.limits:
                if "cpu" in resources.limits:
                    total_lim_cpu += parse_cpu(resources.limits["cpu"])
                if "memory" in resources.limits:
                    total_lim_mem += parse_memory(resources.limits["memory"])

        POD_REQUEST_CPU.labels(namespace=ns, pod=name).set(total_req_cpu)
        POD_REQUEST_MEM.labels(namespace=ns, pod=name).set(total_req_mem)
        POD_LIMIT_CPU.labels(namespace=ns, pod=name).set(total_lim_cpu)
        POD_LIMIT_MEM.labels(namespace=ns, pod=name).set(total_lim_mem)
    
    for item in pod_metrics["items"]:
        ns = item["metadata"]["namespace"]
        pod = item["metadata"]["name"]

        cpu_total = 0
        mem_total = 0

        for c in item["containers"]:
            cname = c["name"]
            cpu = parse_cpu(c["usage"]["cpu"])
            mem = parse_memory(c["usage"]["memory"])

            cpu_total += cpu
            mem_total += mem

            POD_CONTAINER_CPU.labels(namespace=ns, pod=pod, container=cname).set(cpu)
            POD_CONTAINER_MEM.labels(namespace=ns, pod=pod, container=cname).set(mem)

        POD_CPU_CORES.labels(namespace=ns, pod=pod).set(cpu_total)
        POD_MEM_BYTES.labels(namespace=ns, pod=pod).set(mem_total)


def main():
    try:
        config.load_incluster_config()
        log.info("Using in-cluster config")
    except config.ConfigException:
        config.load_kube_config()
        log.info("Using local kubeconfig")

    port = int(os.getenv("METRICS_PORT", "8000"))
    interval = int(os.getenv("SCRAPE_INTERVAL", "30"))

    start_http_server(port)
    log.info("Prometheus metrics server started on port %d", port)

    while True:
        try:
            collect()
        except ApiException as e:
            log.error("Kubernetes API error: %s", e)
        except Exception as e:
            log.error("Failed to collect metrics: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
