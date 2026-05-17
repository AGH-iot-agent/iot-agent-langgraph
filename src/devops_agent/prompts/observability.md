# Observability Query Catalog

Use these queries when diagnosing cluster health. Replace `$NS` with the target namespace
(`iotag-dev` or `iotag-sbx`) and `$SVC` with the service/app label value (e.g. `iot-agent-gateway-api`).

Always run a **Prometheus instant query first** to confirm an issue is still active before proposing a fix.

---

## PromQL — Pod Health

```promql
# Pods stuck in CrashLoopBackOff
kube_pod_container_status_waiting_reason{namespace="$NS", reason="CrashLoopBackOff"} > 0

# Container exit reason in last terminated state (OOMKilled, Error, etc.)
kube_pod_container_status_last_terminated_reason{namespace="$NS"}

# Restart rate over 15 min window (non-zero = unstable pod)
rate(kube_pod_container_status_restarts_total{namespace="$NS"}[15m]) > 0

# Pods not in Ready state
kube_pod_status_ready{namespace="$NS", condition="false"} > 0

# Pods not in Running phase
kube_pod_status_phase{namespace="$NS", phase!="Running"} > 0

# Count of running pods per namespace
count(kube_pod_status_phase{namespace="$NS", phase="Running"})
```

---

## PromQL — Service Health (Spring Boot Actuator)

```promql
# 5xx error rate per service (req/s, 5-min window)
sum by (app) (rate(http_server_requests_seconds_count{namespace="$NS", status=~"5.."}[5m]))

# Overall error rate ratio (5xx / total)
sum(rate(http_server_requests_seconds_count{namespace="$NS", status=~"5.."}[5m]))
/
sum(rate(http_server_requests_seconds_count{namespace="$NS"}[5m]))

# P99 latency per service (seconds)
histogram_quantile(0.99, sum by (app, le) (rate(http_server_requests_seconds_bucket{namespace="$NS"}[5m])))

# P50 latency per service (seconds)
histogram_quantile(0.50, sum by (app, le) (rate(http_server_requests_seconds_bucket{namespace="$NS"}[5m])))

# Request throughput per service (req/s)
sum by (app) (rate(http_server_requests_seconds_count{namespace="$NS"}[5m]))

# Request throughput for a specific service (1-min spike detection)
sum(rate(http_server_requests_seconds_count{namespace="$NS", app="$SVC"}[1m]))
```

---

## PromQL — JVM Internals

```promql
# Heap memory used per service (bytes)
sum by (app) (jvm_memory_used_bytes{namespace="$NS", area="heap"})

# Non-heap memory used per service (bytes)
sum by (app) (jvm_memory_used_bytes{namespace="$NS", area="nonheap"})

# GC pause rate (seconds/s — high value = GC pressure)
rate(jvm_gc_pause_seconds_sum{namespace="$NS"}[5m])

# Live thread count per service
jvm_threads_live_threads{namespace="$NS"}

# Heap used ratio (used / max) — >0.9 means near OOM
sum by (app) (jvm_memory_used_bytes{namespace="$NS", area="heap"})
/
sum by (app) (jvm_memory_max_bytes{namespace="$NS", area="heap"})
```

---

## PromQL — Infrastructure (CPU)

```promql
# CPU usage per pod (cores, 5-min window)
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="$NS", container!=""}[5m]))

# CPU usage per service/app label
sum by (app) (rate(container_cpu_usage_seconds_total{namespace="$NS", container!=""}[5m]))
```

---

## LogQL — Log Pattern Matching

Replace `$NS` with namespace, `$SVC` with the value of the `app` label.

```logql
# All ERROR / FATAL / Exception lines in namespace
{namespace="$NS"} |~ "(?i)(ERROR|FATAL|Exception)"

# Spring Boot application startup failure
{namespace="$NS"} |~ "APPLICATION FAILED TO START"

# Spring Boot started successfully (confirm healthy restart)
{namespace="$NS", app="$SVC"} |~ "Started .* in [0-9.]+ seconds"

# Database connection failures (HikariPool / JDBC)
{namespace="$NS"} |~ "(?i)(HikariPool|Connection is not available|request timed out|Cannot load.*driver|jdbc.*refused|JDBC.*Exception)"

# MQTT broker connectivity
{namespace="$NS"} |~ "(?i)(mqtt.*disconnect|broker.*unavailable|Connection.*refused.*1883|CONNACK.*error)"

# Authentication / JWT / 401 / 403 errors
{namespace="$NS"} |~ "(?i)(401|403|Unauthorized|Forbidden|jwt.*invalid|token.*expired|signature.*verification)"

# Kubernetes secret or ConfigMap missing at runtime
{namespace="$NS"} |~ "(?i)(secret.*not found|configmap.*not found|No secret|SecretNotFoundException)"

# OOMKilled / process killed by kernel
{namespace="$NS"} |~ "(?i)(OOMKilled|Killed process|exit code 137|signal: killed)"

# Helm / Kubernetes deploy errors (from CI logs)
{namespace="$NS"} |~ "(?i)(helm.*failed|Error: UPGRADE FAILED|cannot patch|admission webhook.*denied)"

# General per-service error stream (last 5 min)
{namespace="$NS", app="$SVC"} |~ "(?i)(error|exception|fatal)" | logfmt | level=~"(error|ERROR|fatal|FATAL|warn|WARN)"
```

---

## Quick Reference

| Namespace   | Environment |
|-------------|-------------|
| `iotag-dev` | Development (primary for in-review PRs) |
| `iotag-sbx` | Sandbox (validation target for fix PRs) |

**Workflow:**
1. Run PromQL instant query → confirm issue still active (if resolved, stop)
2. Run LogQL → identify root cause from log lines
3. Cross-check pod state via `k8s_get_pods` + `k8s_get_events`
4. Propose minimal fix, validate with Helm dry-run on `iotag-sbx`
