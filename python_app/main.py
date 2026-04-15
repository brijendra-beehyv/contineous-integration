from fastapi import FastAPI, Request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import time

app = FastAPI()

# 🔹 Metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", 'endpoint', 'http_status']
)

REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency',
    ['endpoint']
)

# 🔹 Middleware to track metrics
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()

    response = await call_next(request)

    latency = time.time() - start_time

    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        http_status=response.status_code
    ).inc()

    REQUEST_LATENCY.labels(
        endpoint=request.url.path
    ).observe(latency)

    return response


# 🔹 Sample endpoint
@app.get('/')
async def root():
    return {"message": "Hello, world!"}


# 🔹 Metrics endpoint for Prometheus
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
