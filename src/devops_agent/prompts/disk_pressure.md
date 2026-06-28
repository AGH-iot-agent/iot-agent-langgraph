# Disk Pressure Response Playbook

## Trigger Events
This prompt applies to: `pvc_disk_pressure`, `node_disk_pressure`

## Context
- **pvc_disk_pressure**: A PersistentVolumeClaim in the namespace has exceeded 80% capacity.
  Writes to the volume (database files, write-ahead logs, application logs) will fail when 100% is reached.
- **node_disk_pressure**: A Kubernetes node has the DiskPressure condition set to True.
  The node will stop scheduling new pods and may evict existing ones.

## Decision Tree

```
Receive event (pvc/node name, namespace, ratio, severity)
        │
        ▼
1. Confirm current state
   k8s_get_pvc_usage(namespace)      ← list all PVCs, capacity, phase
   k8s_get_node_conditions()         ← confirm DiskPressure=True on which nodes
        │
        ▼
2. Identify what is consuming space
   prom_query: kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes
   prom_query_range: same query, last_minutes=30   ← fill rate trend
        │
        ▼
3. Check service logs for write errors
   loki_get_logs(namespace, service, last_minutes=15)
   Look for: "No space left on device", "disk full", "ENOSPC", "write error"
             "could not write to file", "WAL write failed", "checkpoint failed"
        │
        ▼
4. Classify fix strategy:
   ┌───────────────────────────────────────────────────────────────┐
   │ A) PVC usage high but fill rate slow (< 1%/hour)             │
   │    → Configure log rotation for application logs             │
   │    → Set retention policy (Loki, Prometheus TSDB limits)     │
   │    → Propose PVC expansion (increase storageClass size)      │
   │                                                               │
   │ B) PVC fill rate fast (> 5%/hour) — likely log explosion     │
   │    → Diagnose: loki_get_logs for ERROR spam from the service │
   │    → Propose fix for the root cause (error loop logging)     │
   │    → AND increase PVC as mitigation                          │
   │                                                               │
   │ C) Node DiskPressure (node filesystem, not PVC)              │
   │    → Likely: container images, /var/log, /tmp accumulation   │
   │    → Propose: docker system prune, log rotation on node      │
   │    → Propose: add node disk size in infrastructure config    │
   └───────────────────────────────────────────────────────────────┘
        │
        ▼
5. Generate PR with fix
```

## Fix Templates

### A — Increase PVC size in Helm values
```yaml
# In Helm/values-dev.yaml:
# original:
persistence:
  enabled: true
  size: 1Gi

# fixed:
persistence:
  enabled: true
  size: 5Gi
  storageClass: "local-path"   # K3s default StorageClass (supports expansion)
```
**Note**: PVC expansion requires `allowVolumeExpansion: true` in the StorageClass.
Verify: `kubectl get storageclass -o yaml | grep allowVolumeExpansion`

### B — Configure log rotation (application-level)
For Spring Boot services, add to `application.yml`:
```yaml
logging:
  logback:
    rollingpolicy:
      max-file-size: 50MB
      max-history: 5
      total-size-cap: 200MB
```

### C — Prometheus/Loki retention limits
For Prometheus TSDB in Helm values:
```yaml
prometheus:
  prometheusSpec:
    retention: 7d          # was: 30d
    retentionSize: 5GB     # hard size cap
```
For Loki:
```yaml
loki:
  limits_config:
    retention_period: 168h   # 7 days
```

### D — Emergency: manual cleanup (do not include in PR — mention as immediate action)
```bash
# Find large files in PVC-backed pods:
kubectl exec -n iotag-dev <pod-name> -- find /data -type f -size +100M -ls
# Clear old logs:
kubectl exec -n iotag-dev <pod-name> -- find /data/logs -name "*.log.*" -mtime +7 -delete
```

## Important Rules
- **Never delete database data files** — only logs, old backups, temp files.
- PVC expansion is **online** for local-path and most cloud provisioners (no pod restart needed).
- If fill rate > 10%/hour, set severity=critical in your response — immediate action needed.
- Always check if Loki/Prometheus are the culprits before touching app PVCs.
- A node DiskPressure event may require cluster-level (infra) intervention beyond Helm — escalate clearly.
