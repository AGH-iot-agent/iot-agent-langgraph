# Scalability & Traffic Spike Response Playbook

## Trigger Events
This prompt applies to: `request_rate_spike`, `high_cpu_usage`, `high_http_latency`, `high_error_rate`

## Decision Tree

```
Receive event (pod, namespace, metric value)
        │
        ▼
1. Verify spike is still active
   prometheus_query: rate(http_server_requests_seconds_count{pod="..."}[1m])
   → If current value < threshold: LOG "spike resolved" → STOP (no fix needed)
        │
        ▼
2. Check current capacity
   k8s_get_rollout_status(namespace)    ← get current replicaCount
   k8s_get_pods(namespace)              ← check pod READY status
        │
        ▼
3. Check for errors (is the spike causing failures?)
   loki_query_range(namespace, last_minutes=5, query contains pod name)
        │
   ┌────┴────────────────────────────────────────┐
   │ Errors in logs?                             │ No errors?
   ▼                                             ▼
4a. Root cause in logs              4b. Pure traffic spike
    → fix errors first                  → scale up
    → THEN scale if needed
        │
        ▼
5. Choose fix:
   A) replicaCount bump (quick, in values-dev.yaml)
   B) HPA config (sustainable, handles future spikes)
   C) Istio rate-limit (if DDoS pattern — external spike with no business value)
        │
        ▼
6. Validate fix (Helm template render + dry-run on iotag-sbx)
        │
        ▼
7. Open PR with fix
```

## Fix Templates

### A — replicaCount bump in values-dev.yaml
```yaml
# original_snippet (include surrounding context):
replicaCount: 1

# fixed_snippet:
replicaCount: 3
```
Use when: no HPA exists, spike is sustained, replicas < 3.

### B — Enable HPA in values-dev.yaml
```yaml
# original_snippet:
autoscaling:
  enabled: false

# fixed_snippet:
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 6
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```
Use when: spike is bursty/recurring and current replicaCount is already ≥ 2.

### C — Istio rate limit (DDoS pattern)
Identify: >10x normal traffic, mostly from unknown sources, errors 429/503 rising.
Propose an `EnvoyFilter` manifest (`Helm/templates/rate-limit.yaml`) with:
```yaml
apiVersion: networking.istio.io/v1alpha3
kind: EnvoyFilter
metadata:
  name: {service}-rate-limit
  namespace: {namespace}
spec:
  workloadSelector:
    labels:
      app: {service}
  configPatches:
    - applyTo: HTTP_FILTER
      match:
        context: SIDECAR_INBOUND
      patch:
        operation: INSERT_BEFORE
        value:
          name: envoy.filters.http.local_ratelimit
          typed_config:
            "@type": type.googleapis.com/udpa.type.v1.TypedStruct
            type_url: type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit
            value:
              stat_prefix: http_local_rate_limiter
              token_bucket:
                max_tokens: 200
                tokens_per_fill: 200
                fill_interval: 1s
              filter_enabled:
                runtime_key: local_rate_limit_enabled
                default_value:
                  numerator: 100
                  denominator: HUNDRED
              filter_enforced:
                runtime_key: local_rate_limit_enforced
                default_value:
                  numerator: 100
                  denominator: HUNDRED
```

## Output Format

For scalability events, output the same CI fix JSON structure:
```json
{
  "root_cause": "High request rate on pod X (N req/s, threshold M req/s) — insufficient replicas",
  "error_message": "<exact Prometheus metric or Loki error line>",
  "description": "Pod X received N req/s sustained for 5min. Current replicaCount=1 causes queue buildup. Increasing to 3 replicas will distribute load.",
  "files": [
    {
      "path": "Helm/values-dev.yaml",
      "original_snippet": "replicaCount: 1",
      "fixed_snippet": "replicaCount: 3"
    }
  ]
}
```

## Rules
- NEVER scale down other services to compensate for one service's traffic.
- NEVER restart pods as the primary response to a traffic spike.
- ALWAYS verify the spike is still active before proposing a fix.
- ALWAYS include validation (Helm render + dry-run) before opening a PR.
- For DDoS: prefer rate-limiting over infinite scaling (cost control).
