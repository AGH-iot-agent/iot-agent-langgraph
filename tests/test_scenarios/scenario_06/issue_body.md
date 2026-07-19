**Hello,**

The CI workflow for `iot-agent-logs` is failing after the recent refactoring that introduced a reusable deployment workflow. The GitHub Actions run ends with the following error:

```
Error: Unrecognized named-value: 'env'. Located at position 1 within expression: env.NAMESPACE
```

The workflow is passing the target namespace and image tag to the reusable workflow using the `env` context inside the `with:` block:

```yaml
# .github/workflows/deploy.yml
jobs:
  set-env:
    runs-on: ubuntu-latest
    outputs:
      namespace: ${{ steps.vars.outputs.namespace }}
    steps:
      - id: vars
        run: echo "namespace=iotag-dev" >> $GITHUB_OUTPUT

  deploy:
    needs: set-env
    uses: ./.github/workflows/reusable-deploy.yml
    with:
      namespace: ${{ env.NAMESPACE }}
      image_tag: ${{ env.IMAGE_TAG }}
    secrets: inherit
```

The `env` context is not available inside `with:` blocks of reusable workflow calls in GitHub Actions. The expression must reference `needs.<job>.outputs.<key>` or use an inline conditional expression instead.

Please fix the workflow so that the namespace and image tag are passed correctly without using the `env` context in the `with:` block.
