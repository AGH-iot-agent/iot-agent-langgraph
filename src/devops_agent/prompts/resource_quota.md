# Resource Quota Exhaustion Response Playbook

## Trigger Events
This prompt applies to: `namespace_quota_exceeded`

## Context
A Kubernetes ResourceQuota in the monitored namespace has exceeded 80% of its hard limit
(or is fully exhausted). New pods, deployments, and Jobs will be **rejected** with:
```
Error from server (Forbidden): pods "..." is forbidden:
exceeded quota: default-quota, requested: pods=1, used: pods=10, limited: pods=10
```

## Decision Tree

```
Receive event (namespace, resource, used, hard, ratio)
        │
        ▼
1. Confirm current quota state
   k8s_get_resource_quota(namespace)
   → List ALL resources: pods, requests.cpu, limits.cpu, requests.memory, limits.memory
        │
        ▼
2. Identify top consumers
   k8s_get_pods(namespace)          ← count running pods per deployment
   prom_query: sort_desc(sum(container_memory_working_set_bytes) by (pod))
   prom_query: sort_desc(sum(rate(container_cpu_usage_seconds_total[5m])) by (pod))
        │
        ▼
3. Check for orphaned / stuck pods
   k8s_get_events(namespace)        ← look for Failed/Evicted/OOMKilled pods still counted
        │
        ▼
4. Classify fix strategy:
   ┌───────────────────────────────────────────────────────────────┐
   │ A) Pod count quota full                                       │
   │    → Check for Evicted/Terminating pods (counted in quota)   │
   │    → If found: kubectl delete pod --field-selector=status.phase=Failed -n {ns} │
   │    → Propose quota increase in namespace ResourceQuota manifest │
   │                                                               │
   │ B) CPU / memory request quota full                            │
   │    → Find service with highest requests (relative to usage)   │
   │    → Propose reducing requests.cpu/requests.memory for over-provisioned services │
   │    → OR increase hard limit in ResourceQuota manifest         │
   │                                                               │
   │ C) CPU / memory limit quota full                              │
   │    → Find service with highest limits (relative to usage)     │
   │    → Propose aligning limits to 2x requests (not 10x)        │
   └───────────────────────────────────────────────────────────────┘
        │
        ▼
5. Fetch Helm values to propose specific fix
   gh_get_file_content(repo, "Helm/values-dev.yaml")
        │
        ▼
6. Generate PR with fix (if applicable)
```

## Fix Templates

### A — Remove Evicted/Failed pods (immediate, safe)
```bash
kubectl delete pod --field-selector=status.phase=Failed -n iotag-dev
kubectl delete pod --field-selector=status.phase=Succeeded -n iotag-dev
```
Then verify quota usage dropped:
```bash
kubectl describe resourcequota -n iotag-dev
```

### B — Reduce over-provisioned resource requests in Helm values
```yaml
# In Helm/values-dev.yaml for the over-requesting service:
# original:
resources:
  requests:
    cpu: 500m
    memory: 512Mi
  limits:
    cpu: 2000m
    memory: 2Gi

# fixed (align to actual usage + 20% headroom):
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

### C — Increase namespace ResourceQuota
```yaml
# In iot-agent-infra/namespace/resource-quota.yaml (or equivalent):
apiVersion: v1
kind: ResourceQuota
metadata:
  name: default-quota
  namespace: iotag-dev
spec:
  hard:
    pods: "20"                  # was: "10"
    requests.cpu: "4"           # was: "2"
    requests.memory: 8Gi        # was: "4Gi"
    limits.cpu: "8"             # was: "4"
    limits.memory: 16Gi         # was: "8Gi"
```

## Important Rules
- **Always prefer cleanup of stale pods before increasing quota** — quota exhaustion is often caused by Failed/Evicted pods accumulating.
- If memory quota is the bottleneck, check `prom_query` JVM heap — if actual usage is low, requests are misconfigured.
- CPU throttling ≠ CPU quota — check `container_cpu_cfs_throttled_seconds_total` if unsure.
- A PR fix MUST include the exact resource being exhausted (pods/cpu/memory), not a generic increase.
- Never propose removing running, Ready pods — only Evicted/Failed/Completed ones.
