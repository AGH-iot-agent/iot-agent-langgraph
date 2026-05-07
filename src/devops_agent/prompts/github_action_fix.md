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
