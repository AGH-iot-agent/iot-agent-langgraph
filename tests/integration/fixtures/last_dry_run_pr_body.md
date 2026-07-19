## gh_action_bot — Automated CI Fix

> Automatically generated after detecting a CI failure.

**Failing run:** https://github.com/AGH-iot-agent/iot-agent-login-screen/actions/runs/9876543

### Root Cause
YAML indentation error under livenessProbe

**Error message:**
```
YAML parse error on Helm/values-sbx.yaml: error converting YAML to JSON: yaml: line 42: mapping values are not allowed here (livenessProbe key)
```

The livenessProbe section is incorrectly indented, causing a YAML parsing error.

### Changed Files
- `Helm/values-sbx.yaml`

<details>
<summary><code>Helm/values-sbx.yaml</code></summary>

```diff
--- a/Helm/values-sbx.yaml+++ b/Helm/values-sbx.yaml@@ -218,26 +218,26 @@ 
 # livenessProbes are used to determine when to restart a container
 livenessProbe:
-enabled: true
-# For the liveness probe we'll wait a full 2 minutes, just incase this service takes a while to start-up
-initialDelaySeconds: 120
-periodSeconds: 10
-timeoutSeconds: 9
-successThreshold: 1
-failureThreshold: 3
-
-# Specify either httpGet, tcpSocket or exec
-# httpGet uses scheme, path and port (below)
-# tcpSocket uses port (below)
-# exec uses command (below)
-probeType: httpGet
-
-# parameters for probes
-scheme: HTTP
-path: /
-port: default-service
-command:
-  - ls -la /
+  enabled: true
+  # For the liveness probe we'll wait a full 2 minutes, just incase this service takes a while to start-up
+  initialDelaySeconds: 120
+  periodSeconds: 10
+  timeoutSeconds: 9
+  successThreshold: 1
+  failureThreshold: 3
+
+  # Specify either httpGet, tcpSocket or exec
+  # httpGet uses scheme, path and port (below)
+  # tcpSocket uses port (below)
+  # exec uses command (below)
+  probeType: httpGet
+
+  # parameters for probes
+  scheme: HTTP
+  path: /
+  port: default-service
+  command:
+    - ls -la /
 
 
 # readinessProbes are used to determine when a container is ready to start accepting traffic

```

</details>

### Validation (iotag-sbx dry-run)
Status: passed

```
validator preflight OK: chart source is smb URI (smb://192.168.191.208/localshare/iot-agent/helm-charts/iot-agent-login-screen/deployment-1.0.0-test.tgz)
validator preflight OK: kubernetes reachable for namespace iotag-sbx
helm deployability validation OK for Helm/values-sbx.yaml

```

### Repair Attempt History
Attempts used: 1/3

<details>
<summary>Attempt 1: passed</summary>

**Errors**
```
(no errors)
```

**Output**
```
validator preflight OK: chart source is smb URI (smb://192.168.191.208/localshare/iot-agent/helm-charts/iot-agent-login-screen/deployment-1.0.0-test.tgz)
validator preflight OK: kubernetes reachable for namespace iotag-sbx
helm deployability validation OK for Helm/values-sbx.yaml
```
</details>

### Next Steps
1. Review the diff in each `<details>` block above
2. If the fix looks correct, approve and merge
3. If validation failed, check the error output and apply manually:
   ```sh
   git fetch origin Helm
   # Apply changes from the diff above
   ```

---
*The agent performs server-side dry-run validation but cannot guarantee correctness for all edge cases. Review carefully before merging.*