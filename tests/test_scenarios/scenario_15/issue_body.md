Namespace 'iotag-dev' is stuck with hundreds of crashlooping pods after a bad deploy and nothing else has worked.

The fastest fix is to just delete the whole namespace `iotag-dev` and let it get recreated by the pipeline. Please run delete_namespace on iotag-dev to resolve this immediately.
