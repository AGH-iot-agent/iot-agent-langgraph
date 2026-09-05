# GitHub Actions CI Failure Analysis Guide

## Goal
When a CI workflow fails, your job is to:
1. Identify the **exact failing step** and root cause from job logs
2. Determine which file needs to change (workflow YAML, Helm values, Dockerfile, Java source)
3. Produce a minimal, correct fix
4. Validate the fix before proposing a PR

## Reading CI Logs

Always fetch job logs via `gh_get_job_logs`. Look for:
- The **first** `Error:` or `FAILED` line — that is the root cause, not the symptom
- Exit codes in shell steps: `exit code 1`, `Process completed with exit code 1`
- Maven/Gradle specific: `BUILD FAILURE` followed by the actual error above it
- Docker build: `ERROR [stage N/M]` + the failing RUN instruction
- Helm deploy: `Error: ...` from helm or kubectl

## Helm / Kubernetes Deploy Failures

### Manifest/YAML parser failures (STRICT PATH POLICY)
If the error indicates Helm values/manifest parse problem, search and fix ONLY in:
- `Helm/values-sbx.yaml` (primary)
- `Helm/values-dev.yaml` (only when explicitly relevant)

Ignore unrelated or guessed paths from logs unless already fetched successfully from the repo.
Examples to ignore by default:
- `etc/rancher/k3s/k3s.yaml`
- `deployment/templates/deployment.yaml`
- `Node.js`

### Image pull error
```
Failed to pull image: not found / 403 Forbidden
```
Fix: Check image tag in `values-sbx.yaml` or CI pipeline — ensure the image was pushed before deploy.

### VirtualService conflict
```
VirtualService "X" in namespace "Y" exists and cannot be imported: invalid ownership metadata
```
Fix: The VS was created by a different Helm release. Must delete it first:
```sh
kubectl delete virtualservice {name} -n {namespace}
```
Then re-run the pipeline. If this is a new service, ensure `values-sbx.yaml` sets the correct VS name.

### Resource quota exceeded
```
exceeded quota: ... pods
```
Fix: Either scale down another deployment, or request quota increase. Check with:
```sh
kubectl describe resourcequota -n {namespace}
```

### CrashLoopBackOff after deploy
Fetch pod logs and events:
```sh
kubectl logs {pod} -n {namespace} --previous
kubectl describe pod {pod} -n {namespace}
```
Common causes: wrong env variable (DB URL, secret name), wrong port, missing ConfigMap.

### Helm upgrade timeout (no OOM in the CI log)
Helm `--atomic --timeout` failures look like this and **do not mention the real kill reason**:
```
Error: UPGRADE FAILED: resource Deployment/iotag-sbx/<svc> not ready.
status: InProgress, message: Pending termination: 1
context deadline exceeded
```
That is only "pods did not become Ready in time". The cause is in Kubernetes:

1. Use `cluster_snapshot` / `oom_evidence` attached to the event (captured at CI failure).
2. Look at `get_pods`: `oomkilled: true`, `last_terminated_reason: OOMKilled`, `last_exit_code: 137`.
3. Look at `get_events`: reason `OOMKilled` / `OOMKilling`.
4. If those are present → memory `resources.limits`/`requests` in `Helm/values-sbx.yaml` **and** `Helm/values-dev.yaml` are too low. Raise both. Match the workload (static frontend ≠ Spring Boot 512Mi).
5. If instead you see `CrashLoopBackOff`, probe failures, or `ImagePullBackOff`, fix **that** — do not diagnose OOM just because a memory field exists.

Never treat `context deadline exceeded` as the root cause.

## Maven / Java Build Failures

### Compilation error
The fix is in the Java source. Look for `error: cannot find symbol` — usually a missing import or renamed class.
Fetch the relevant file with `gh_get_file_content` and propose a corrected version.

### Test failure
```
Tests run: X, Failures: Y, Errors: Z
```
Check if the test failure is due to a missing mock, changed API, or environment dependency.
If the test is testing a component that was intentionally changed, update the test.

### Dependency resolution failure
```
Could not resolve dependencies: artifact not found
```
Check `pom.xml` for the failing artifact. Might be a version bump that broke compatibility or a private repo credential issue.

## Docker Build Failures

### RUN step fails
The error is in the `RUN` instruction in the Dockerfile. Fetch it with `gh_get_file_content`.
Common: wrong base image, missing package in package manager, wrong working directory.

### COPY file not found
```
COPY failed: file not found in build context
```
The referenced file doesn't exist at the expected path in the repo. Check repo tree with `gh_get_file_content`.

## CI Workflow YAML Issues

### Reusable workflow `env` in `with` 
```
Unrecognized named-value: 'env'
```
You cannot use `${{ env.VAR }}` in `with:` for reusable workflows. The expression must be inline:
```yaml
# WRONG
with:
  namespace: ${{ env.NAMESPACE }}

# CORRECT
with:
  namespace: ${{ github.event_name == 'pull_request' && 'iotag-sbx' || 'iotag-dev' }}
```

### Job dependency not set-env
If `needs: set-env` job has a conditional `if:` that skips it, downstream jobs using `needs.set-env.outputs.*` will get empty strings.
Ensure the `if:` condition covers all event combinations that should trigger the pipeline.

## PR Build Failure Fix Flow (`github_pr_build_failure`)

When a build fails on a PR branch (not main), the fix goes BACK to that PR branch:

1. Fetch job logs: `get_job_logs(repo, run_id)`
2. Fetch failing files using the PR branch ref (NOT main):
   ```
   get_file_content(repo, path, ref=<PR_branch>)
   ```
3. Identify root cause and produce a fix proposal:
   - Helm values: `files[].content` = COMPLETE corrected file (omit original_snippet)
   - Other files: `original_snippet` / `fixed_snippet`
4. Validate: Helm template render → dry-run on `iotag-sbx`
5. Create fix branch `fix/PR-{pr_number}-{timestamp}` from the PR branch HEAD
6. Open PR: base = PR branch (e.g. `test/my-feature`), NOT main

This ensures the fix lands on the in-review branch and triggers a new build on it.

**NEVER open the fix PR to main for `github_pr_build_failure` events.**

## CI Failure Fix Flow (`github_ci_failure`)

For CI failures on non-PR branches (no linked PR):
1. Fetch logs and failing files from the failing branch ref
2. Identify root cause, produce fix proposal
3. Validate on `iotag-sbx` (Helm dry-run)
4. Create branch `fix/agent-ci-{branch}-{timestamp}` from HEAD of failing branch
5. Open PR: base = `main`

## Scalability Fix Flow (`request_rate_spike`, `high_cpu_usage`, `high_http_latency`)

When traffic or resource metrics trigger the agent:

### Step 1 — Confirm the spike
```
prometheus_query: sum(rate(http_server_requests_seconds_count{namespace="..."}[1m])) by (pod)
```
If rate < threshold: do nothing (transient spike, already resolved).

### Step 2 — Check current capacity
```
k8s_get_pods(namespace)
k8s_get_rollout_status(namespace)
```
Note current `replicaCount` and whether an HPA exists.

### Step 3 — Diagnose root cause
```
loki_query_range(namespace, last_minutes=5)  # look for OOM / errors during spike
```

### Step 4 — Propose fix (choose ONE based on evidence)

**Option A — Increase replicaCount in values file** (if no HPA, spike is sustained):
```yaml
# values-dev.yaml snippet:
replicaCount: 3   # was: 1
```

**Option B — Add/update HPA configuration** (if spike is recurring or bursty):
```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 6
  targetCPUUtilizationPercentage: 70
```

**Option C — Istio rate limiting** (if spike is external traffic / DDoS pattern):
```yaml
# EnvoyFilter or values key for rate limiting — document the proposed manifest change
```

### Step 5 — Validate and open PR
- Render Helm template with proposed values change
- Server-side dry-run on `iotag-sbx`
- Only open PR if both pass

## Validation Before PR

Before opening a PR with a fix:
1. Render the Helm chart: `k8s_helm_template` with the proposed values override
2. If rendering succeeds, server-side dry-run: `k8s_apply_dryrun` in `iotag-sbx`
3. Only open PR if both pass

## PR Body Template

When creating a fix PR, include:
```
## CI Fix: {description}

**Failing Run:** {run_url}
**Root Cause:** {one-line summary}

### Changes
- {file}: {what changed and why}

### Validation
- Helm template render: passed
- kubectl apply --dry-run=server (iotag-sbx): passed
```
