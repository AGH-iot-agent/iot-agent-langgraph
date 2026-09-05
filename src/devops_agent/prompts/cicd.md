# CI/CD Pipeline — What the Agent Must Know

## Helm values files: what CI/CD injects automatically

The following fields are **intentionally empty or absent** in `Helm/values-*.yaml` in the
repository. They are injected by the CI/CD pipeline at deploy time.
**Do NOT treat them as errors. Do NOT propose setting `name`, `image.repository`, or
`image.tag` — the pipeline sets these automatically.**

| Field | Set by | Notes |
|---|---|---|
| `name` | CI pipeline | `name: ""` in repo is correct — do NOT fix |
| `image.repository` | CI pipeline | empty in repo is correct — do NOT fix |
| `image.tag` | CI pipeline | empty in repo is correct — do NOT fix |
| `global.image.tag` | CI pipeline | empty in repo is correct — do NOT fix |

### `helm template` always fails locally with `A valid Values.name is required!`
This is EXPECTED when running against the repo copy of `values-*.yaml`.
The sandbox validator pre-fills `name` and `image.repository` before rendering.
**If you see this error in validation output, ignore it and look for the REAL cause.**

## Fields that ARE bugs when wrong

| Field | iotag-dev value | iotag-sbx value | Error symptom |
|---|---|---|---|
| `global.namespace` | `iotag-dev` | `iotag-sbx` | `UPGRADE FAILED: resource already exists` |
| `replicaCount` | integer e.g. `1` | integer | `cannot unmarshal string into Go value of type int32` |
| `resources.limits.memory` | enough for the workload | same | Helm: `not ready` / `context deadline exceeded` (OOMKilled is **not** in CI logs) |
| `livenessProbe.*` | indented under `livenessProbe:` | same | `cannot unmarshal bool` / YAML parse error |
| `readinessProbe.*` | indented under `readinessProbe:` | same | same |

## Common CI failure patterns

## Search Scope for Manifest Problems (MANDATORY)

When CI error is about Helm values, manifest parsing, orphaned probe fields, or YAML shape:
1. Search ONLY in `Helm/values-*.yaml` within the target repository.
2. Primary file for sbx deploy validation is `Helm/values-sbx.yaml`.
3. Do NOT use guessed paths that were not fetched successfully.
4. If values file cannot be fetched, return no file fix proposal (`files: []`) instead of inventing paths.

### `UPGRADE FAILED: rendered manifests contain a resource that already exists`
Root cause: `global.namespace: iotag-dev` in `values-sbx.yaml` (should be `iotag-sbx`).
Fix: `global.namespace: iotag-sbx`.

### `cannot unmarshal string into Go value of type int32`
Root cause: `replicaCount` is a quoted string e.g. `"not-a-number"` instead of integer.
Fix: `replicaCount: 1` (no quotes).

### Probe YAML indentation error / `cannot unmarshal bool`
Root cause: `livenessProbe` sub-fields are at root level instead of nested.

**Broken (root-level — fields NOT indented under livenessProbe):**
```yaml
livenessProbe:
enabled: true
initialDelaySeconds: 120
periodSeconds: 10
```

**Fixed (correctly nested):**
```yaml
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

### Orphaned probe sub-fields / `ERROR: '<field>' at root level`
Root cause: the probe block was partially fixed but the original misindented lines were NOT removed.
This leaves orphan keys (`enabled`, `initialDelaySeconds`, `periodSeconds`, `timeoutSeconds`,
`successThreshold`, `failureThreshold`, `probeType`, `scheme`, `path`, `port`, `command`) sitting
at root level in the file.  The sandbox validator will report these as explicit errors.

**Broken — probe block fixed but orphans remain at root level:**
```yaml
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
enabled: true              # <-- ORPHAN at root
initialDelaySeconds: 120   # <-- ORPHAN at root
periodSeconds: 10          # <-- ORPHAN at root
timeoutSeconds: 9          # <-- ORPHAN at root
successThreshold: 1        # <-- ORPHAN at root
failureThreshold: 3        # <-- ORPHAN at root
```

**Fix strategy — TWO entries in `files[]`:**

Entry 1 — fix the probe block indentation (if not already correct).
Entry 2 — remove the orphaned root-level lines:
```
original_snippet: include 2 lines of context before and after the orphaned block so the match
  is unambiguous, then list the orphaned lines verbatim.
fixed_snippet: same context lines, orphaned lines removed entirely.
```

**MANDATORY CHECK:** Whenever you fix a probe indentation issue, you MUST:
1. Scan the ENTIRE file for any of these keys at root level:
   `enabled`, `initialDelaySeconds`, `periodSeconds`, `timeoutSeconds`,
   `successThreshold`, `failureThreshold`, `probeType`, `scheme`, `path`, `port`, `command`
2. If any are found at root level, add a second `files[]` entry to remove them.
3. Failing to remove orphaned fields will cause the sandbox validator to reject your fix.

**If orphaned fields span multiple places and a short snippet cannot target them all,
use the `content` key with the FULL corrected file instead of `original_snippet`/`fixed_snippet`:**

```json
{
  "files": [
    {
      "path": "Helm/values-sbx.yaml",
      "content": "<FULL corrected file content here — no orphaned fields"
    }
  ]
}
```

When using `content` key: omit `original_snippet` and `fixed_snippet` entirely.
The validator and pr_creator both accept this format as an explicit full-file replacement.
This is the PREFERRED strategy when you receive a sandbox validator HINT message about orphaned
fields surviving after a snippet-based fix attempt.

## Workflow files

All services use `.github/workflows/ci.yml` as the primary workflow.
`build.yml` does NOT exist in any service repo — a 404 for it is normal, ignore it completely.
Focus only on `ci.yml` logs.

## Environment namespace mapping

| Environment | Namespace | values file |
|---|---|---|
| Development | `iotag-dev` | `Helm/values-dev.yaml` |
| Sandbox/Staging | `iotag-sbx` | `Helm/values-sbx.yaml` |
