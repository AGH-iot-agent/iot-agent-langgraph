# Helm Values Reference — Universal Deployment Chart

This document is the authoritative schema for `Helm/values-*.yaml` files in the iot-agent platform.
Use it to validate, fix, and write Helm values files. Every field listed here must be at the exact
nesting level shown. Fields at the wrong nesting level are ALWAYS a bug.

---

## Top-Level Fields

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `name` | string | `""` | **CI injects** | Service name. Empty in repo is CORRECT — CI fills it. Never hardcode. |
| `replicaCount` | **integer** | `1` | no | Number of pod replicas. MUST be a bare integer — never a quoted string. |
| `minReadySeconds` | int or false | `false` | no | Minimum seconds pod must be ready before considered available. |
| `revisionHistoryLimit` | integer | `10` | no | How many old ReplicaSets to keep. |
| `priorityClassName` | string | `""` | no | Kubernetes PriorityClass name for pod scheduling. |
| `shareProcessNamespace` | bool | `false` | no | Share process namespace between containers. May break containers expecting PID 1. |
| `hostname` | string | `""` | no | Override pod hostname. |
| `tty` | bool | `false` | no | Enable stdin/tty for containers. |
| `usingNewRecommendedLabels` | bool | `false` | no | Enable new `app.kubernetes.io/*` label scheme (k8s 1.20+). |
| `labelsEnableDefault` | bool | `true` | no | Add default `app: <name>` label to all resources. |
| `labels` | map | `{}` | no | Additional labels applied to pods and services. |
| `podAnnotations` | map | `{}` | no | Additional annotations on pods. |
| `lifecycle` | map | `{}` | no | Lifecycle hooks (postStart, preStop). |
| `globalEnvs` | list | `[]` | no | Primary global env vars for all containers. |
| `globalEnvsSecondary` | list | `[]` | no | Secondary global env vars (multi-inheritance helper). |
| `extraEnvs` | list | `[]` | no | Environment-specific env vars (dev/sbx overrides). |
| `envFrom` | list | `[]` | no | Pull env vars from ConfigMaps or Secrets. |
| `usingMemoryKibiBytesEnvs` | bool | `false` | no | Deprecated: memory kibibytes env vars. |
| `nodeSelector` | map | `{}` | no | Schedule pods to nodes matching these labels. |
| `tolerations` | list | `[]` | no | Tolerate node taints. |
| `securityGroupIDs` | list | `[]` | no | AWS Security Group IDs. |
| `volumes` | list | `[]` | no | Extra volumes added to the pod. |
| `volumeMounts` | list | `[]` | no | Extra volume mounts for the main container. |
| `initContainers` | list | `[]` | no | Init containers to run before main container. |
| `extraContainers` | list | `[]` | no | Sidecar containers added to the pod. |
| `resources` | map | `{}` | no | Container CPU/memory requests and limits. |

---

## `image` Block

Nested under `image:` — these fields MUST be indented under `image:`.

| Field | Type | Default | Description |
|---|---|---|---|
| `image.tag` | string | `""` | **CI injects** — empty in repo is CORRECT. |
| `image.repository` | string | `""` | **CI injects** — empty in repo is CORRECT. |
| `image.command` | list | `[]` | Override Docker ENTRYPOINT. |
| `image.args` | list | `[]` | Override Docker CMD arguments. |
| `image.imagePullPolicy` | string | `IfNotPresent` | `IfNotPresent` or `Always`. |
| `image.imagePullSecrets` | list | `[]` | Secret names for private registries. |

---

## `service` Block

Nested under `service:`.

| Field | Type | Default | Description |
|---|---|---|---|
| `service.enabled` | bool | `true` | Create a Kubernetes Service. |
| `service.type` | string | `ClusterIP` | `ClusterIP`, `NodePort`, or `LoadBalancer`. |
| `service.port` | integer | `80` | Service port. |
| `service.targetPort` | integer | `80` | Container port the service forwards to. Override this for Spring Boot apps (e.g. `8080`). |
| `service.name` | string | `default-service` | Service port name. Used as `port` reference in probes. |
| `service.annotations` | map | `{}` | Service annotations. |
| `service.additionalPorts` | list | `[]` | Extra named ports. |

---

## `livenessProbe` Block

**ALL sub-fields MUST be indented under `livenessProbe:`.**
Sub-fields at root level are an INDENTATION BUG that causes YAML parse errors.

| Field | Type | Default | Description |
|---|---|---|---|
| `livenessProbe.enabled` | bool | `true` | Enable liveness probe. |
| `livenessProbe.initialDelaySeconds` | integer | `120` | Seconds to wait before first probe. |
| `livenessProbe.periodSeconds` | integer | `10` | Probe interval in seconds. |
| `livenessProbe.timeoutSeconds` | integer | `9` | Probe timeout. |
| `livenessProbe.successThreshold` | integer | `1` | Successes needed to mark healthy. |
| `livenessProbe.failureThreshold` | integer | `3` | Failures before restarting container. |
| `livenessProbe.probeType` | string | `httpGet` | `httpGet`, `tcpSocket`, or `exec`. |
| `livenessProbe.scheme` | string | `HTTP` | HTTP scheme (`HTTP` or `HTTPS`). |
| `livenessProbe.path` | string | `/alive` | HTTP path for httpGet probe. Override for Spring Boot: `/actuator/health/liveness`. |
| `livenessProbe.port` | string | `default-service` | Port name or number. Must match `service.name`. |
| `livenessProbe.command` | list | `["ls -la /"]` | Command for exec probe type. |

---

## `readinessProbe` Block

**ALL sub-fields MUST be indented under `readinessProbe:`.**
Sub-fields at root level are an INDENTATION BUG.

| Field | Type | Default | Description |
|---|---|---|---|
| `readinessProbe.enabled` | bool | `true` | Enable readiness probe. |
| `readinessProbe.initialDelaySeconds` | integer | `5` | Seconds to wait before first probe. |
| `readinessProbe.periodSeconds` | integer | `5` | Probe interval in seconds. |
| `readinessProbe.timeoutSeconds` | integer | `4` | Probe timeout. |
| `readinessProbe.successThreshold` | integer | `2` | Successes needed to mark ready. |
| `readinessProbe.failureThreshold` | integer | `2` | Failures before removing from service. |
| `readinessProbe.probeType` | string | `httpGet` | `httpGet`, `tcpSocket`, or `exec`. |
| `readinessProbe.scheme` | string | `HTTP` | HTTP scheme. |
| `readinessProbe.path` | string | `/ready` | HTTP path. Override for Spring Boot: `/actuator/health/readiness`. |
| `readinessProbe.port` | string | `default-service` | Port name or number. |
| `readinessProbe.command` | list | `["ls -la /"]` | Command for exec probe type. |

---

## `ingress` and `ingress_secondary` Blocks

| Field | Type | Default | Description |
|---|---|---|---|
| `ingress.enabled` | bool | `false` | Create an Ingress resource. Not used in iot-agent (uses Istio). |
| `ingress.nginx_class` | string | `infrastructure` | nginx ingress controller class. |
| `ingress.pathType` | string | `ImplementationSpecific` | `ImplementationSpecific` or `Prefix`. |
| `ingress.hosts` | list | chart-example.local | List of `{host, paths}` objects. |
| `ingress.tls` | list | `[]` | TLS config. |
| `ingress.annotations` | map | `{}` | Ingress annotations. |

---

## `istio` Block

Used in iot-agent instead of Ingress.

| Field | Type | Default | Description |
|---|---|---|---|
| `istio.virtualService.enabled` | bool | `false` | Create Istio VirtualService. |
| `istio.virtualService.hosts` | list | `[chart-example.local]` | Hostnames the VS matches. |
| `istio.virtualService.gateways` | list | `[mesh]` | Gateways: use `istio-system/istio-ingressgateway` for external traffic. |
| `istio.virtualService.http` | list | `[]` | Full HTTP routing spec. If empty, chart generates a default route. |
| `istio.gateway.createIfMissing` | bool | `false` | Create Gateway if not present. |
| `istio.gateway.namespace` | string | `istio-system` | Namespace of the Istio ingress gateway. |
| `istio.gateway.name` | string | `istio-ingressgateway` | Name of the Istio ingress gateway. |

---

## `autoscaling` Block

| Field | Type | Default | Description |
|---|---|---|---|
| `autoscaling.enabled` | bool | `false` | Enable HorizontalPodAutoscaler. |
| `autoscaling.minReplicas` | integer | `2` | Minimum replicas. |
| `autoscaling.maxReplicas` | integer | `8` | Maximum replicas. |
| `autoscaling.targetCPUUtilizationPercentage` | integer | `75` | CPU trigger threshold. |
| `autoscaling.targetMemoryUtilizationPercentage` | integer | `90` | Memory trigger threshold. |

---

## `podDistuptionBudget` Block

| Field | Type | Default | Description |
|---|---|---|---|
| `podDistuptionBudget.enabled` | bool | `true` | Enable PDB (only active if replicaCount > 1). |
| `podDistuptionBudget.minAvailable` | integer | `1` | Minimum available pods during disruption. |

---

## `deploymentStrategy` Block

| Field | Type | Default | Description |
|---|---|---|---|
| `deploymentStrategy.type` | string | `RollingUpdate` | `RollingUpdate` or `Recreate`. |
| `deploymentStrategy.rollingUpdate.maxUnavailable` | string | `25%` | Max pods unavailable during rollout. |
| `deploymentStrategy.rollingUpdate.maxSurge` | string | `25%` | Max extra pods during rollout. |

---

## `global` Block

| Field | Type | Default | Description |
|---|---|---|---|
| `global.image.tag` | string | `""` | **CI injects** — empty in repo is CORRECT. |
| `global.namespace` | string | `""` | **Environment-specific — CRITICAL.** Must be `iotag-dev` in `values-dev.yaml` and `iotag-sbx` in `values-sbx.yaml`. Cross-setting is FORBIDDEN. |

---

## `serviceAccount` Block

| Field | Type | Default | Description |
|---|---|---|---|
| `serviceAccount.enabled` | bool | `false` | Create a ServiceAccount. |
| `serviceAccount.annotations` | map | `{}` | Annotations (e.g. IAM role ARN). |

---

## `rbac` Block

| Field | Type | Default | Description |
|---|---|---|---|
| `rbac.create` | bool | `false` | Create RBAC Role and RoleBinding. |
| `rbac.clusterWideAccess` | bool | `false` | Use ClusterRole instead of namespaced Role. |
| `rbac.rules` | list | `[]` | RBAC policy rules. |

---

## `security` Block (DEPRECATED)

**Do not use.** Use `podSecurityContext` and `containerSecurityContext` instead.

---

## CRITICAL: Probe Field Nesting — Anti-Patterns

### BROKEN — Orphaned probe sub-fields at root level

This is caused when the probe block is fixed but the original misindented lines are NOT removed.
The root-level `enabled`, `initialDelaySeconds`, etc. are INVALID and cause `helm template` to fail
or produce unexpected behavior.

```yaml
# WRONG — sub-fields at root level (orphaned leftovers from broken config)
livenessProbe:
  enabled: true
  initialDelaySeconds: 120
  periodSeconds: 10
  timeoutSeconds: 9
  successThreshold: 1
  failureThreshold: 3
  probeType: httpGet
  scheme: HTTP
  path: /actuator/health/liveness
  port: default-service
enabled: true                  # <-- ORPHAN — must not exist at root level
initialDelaySeconds: 120       # <-- ORPHAN
periodSeconds: 10              # <-- ORPHAN
timeoutSeconds: 9              # <-- ORPHAN
successThreshold: 1            # <-- ORPHAN
failureThreshold: 3            # <-- ORPHAN
```

### FIXED — All sub-fields properly nested, orphans removed

```yaml
# CORRECT — complete probe block, no root-level orphans
livenessProbe:
  enabled: true
  initialDelaySeconds: 120
  periodSeconds: 10
  timeoutSeconds: 9
  successThreshold: 1
  failureThreshold: 3
  probeType: httpGet
  scheme: HTTP
  path: /actuator/health/liveness
  port: default-service
```

---

## Orphaned Field Detection Rules

The following field names are **ONLY valid when nested under `livenessProbe:` or `readinessProbe:`**.
If any of these appear as root-level keys in a values file, they are BUGS and MUST be removed:

```
enabled
initialDelaySeconds
periodSeconds
timeoutSeconds
successThreshold
failureThreshold
probeType
scheme
path
port
command
```

**Rule:** When generating a fix that corrects probe indentation, you MUST:
1. Fix the indentation of the probe block itself.
2. Scan the REST of the file for any of the above keys at root level.
3. If orphaned root-level keys are found, include a SECOND entry in `files[]` that removes them.
4. The `original_snippet` for the cleanup entry must include enough context lines to uniquely
   identify the orphaned block's position in the file.
