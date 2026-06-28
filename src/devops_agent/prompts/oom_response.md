# OOMKilled Pod Response Playbook

## Trigger Events
This prompt applies to: `k8s_pod_oomkilled`

## Context
A container was killed by the Linux OOM (Out-Of-Memory) Killer because it exceeded its
`resources.limits.memory` setting. Exit code 137 (SIGKILL). Kubernetes will restart the
container (CrashLoopBackOff if it keeps happening).

**Two root causes** — must distinguish:
1. **Memory limit too low**: Container is healthy but limit is set below what the JVM/app needs.
   → Fix: Increase `limits.memory` (and `requests.memory`) in Helm values.
2. **Memory leak / unbounded growth**: Container memory keeps growing — GC cannot reclaim.
   → Fix: Find the leak (heap dump, GC logs), AND increase limit as temporary mitigation.

## Decision Tree

```
Receive event (pod_name, namespace, restarts)
        │
        ▼
1. Confirm OOMKilled and get current limits
   k8s_describe_pod(pod_name, namespace)
   → Look for: "OOMKilled", "Exit Code: 137", "Limits: memory: XXX"
        │
        ▼
2. Analyse JVM heap trend (last 30 min)
   prom_query_range:
     jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes{area="heap"}
   
   Pattern A — Gradual linear increase → MEMORY LEAK
   Pattern B — Stable usage hitting a hard ceiling → LIMIT TOO LOW
        │
        ▼
3. Check container memory working set
   prom_query_range: container_memory_working_set_bytes{pod=~"service-name.*"}
   → Confirms peak usage before kill
        │
        ▼
4. Check pod logs for OOM evidence
   k8s_get_pod_logs(namespace, pod_name, tail=200)
   Look for: "OutOfMemoryError", "GC overhead limit exceeded",
             "Cannot allocate memory", "java.lang.OutOfMemoryError: Java heap space"
        │
        ▼
5. Determine fix:
   ┌────────────────────────────────────────────────────────────────┐
   │ Pattern B (limit too low) — most common in this stack:        │
   │   Spring Boot needs: 256Mi minimum (no features)              │
   │                      512Mi standard (with actuator, metrics)  │
   │                      1Gi  for high-traffic / JPA services     │
   │   Formula: requests = 60-70% of limits                        │
   │                                                               │
   │ Pattern A (memory leak):                                      │
   │   → Increase limit as TEMPORARY fix (keep service alive)      │
   │   → Add diagnostic: enable JVM GC logging, heap dump on OOM  │
   │   → Comment: "Memory leak suspected — increase is temporary"  │
   └────────────────────────────────────────────────────────────────┘
        │
        ▼
6. Fetch current limits from GitHub Helm values
   gh_get_file_content(repo, "Helm/values-dev.yaml")
        │
        ▼
7. Generate PR with memory limit increase
```

## Fix Templates

### A — Increase memory limits (most common case)
```yaml
# In Helm/values-dev.yaml:
# original (too low for Spring Boot):
resources:
  requests:
    memory: 64Mi
  limits:
    memory: 128Mi

# fixed (standard Spring Boot service):
resources:
  requests:
    memory: 256Mi
  limits:
    memory: 512Mi
```

### B — High-traffic JPA service (e.g. iot-agent-device-api, iot-agent-stream-worker)
```yaml
resources:
  requests:
    cpu: 200m
    memory: 512Mi
  limits:
    cpu: 1000m
    memory: 1Gi
```

### C — Enable JVM GC diagnostic logging (if leak suspected)
Add to `Helm/values-dev.yaml` under `env`:
```yaml
env:
  - name: JAVA_TOOL_OPTIONS
    value: >-
      -XX:+HeapDumpOnOutOfMemoryError
      -XX:HeapDumpPath=/tmp/heapdump.hprof
      -Xlog:gc*:file=/tmp/gc.log:time,uptime:filecount=3,filesize=10m
```

## Minimum Memory Reference (Spring Boot / Java 17)
| Service Type                          | requests.memory | limits.memory |
|---------------------------------------|-----------------|---------------|
| Minimal (no JPA, no metrics)          | 128Mi           | 256Mi         |
| Standard (actuator, metrics, JPA)     | 256Mi           | 512Mi         |
| High-traffic (gateway, stream-worker) | 512Mi           | 1Gi           |
| With Istio sidecar (envoy)            | +64Mi requests  | +128Mi limits |

## Important Rules
- **Always check the JVM heap trend** before recommending a fix — do not blindly increase limits.
- If `jvm_memory_max_bytes` equals `limits.memory`, the JVM was given the full container limit → increase both.
- If JVM max << limits.memory, the container was killed by non-heap memory (native, metaspace, threads) — check `-Xmx` JVM flag.
- A PR must include BOTH `requests.memory` AND `limits.memory` — never change just one.
- Requests should be 50-70% of limits to allow node scheduling flexibility.
- After merging, verify with: `kubectl top pod <pod-name> -n iotag-dev --containers`
