# Istio Configuration Reference — iot-agent

## Routing Model: VirtualService Only (No Ingress)

This project uses **Istio VirtualService exclusively** for HTTP routing. There is no NGINX Ingress or Traefik Ingress controller.
Any reference to `ingress:` in Helm values is intentionally disabled (`enabled: false`).

## Gateway

```
Name:       istio-ingressgateway
Namespace:  istio-system
Selector:   istio=ingressgateway
Reference:  istio-system/istio-ingressgateway
```

All VirtualServices bind to this gateway. Do not create new gateways unless explicitly required.

## VirtualService Structure

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: {service-name}
  namespace: {namespace}
spec:
  hosts:
    - {service-name}-{env}.iotag-{env}.com
  gateways:
    - istio-system/istio-ingressgateway
  http:
    - match:
        - uri:
            prefix: /
      route:
        - destination:
            host: {service-name}.{namespace}.svc.cluster.local
            port:
              number: 80
```

## Naming Conventions

| Field        | Pattern                                    | Example (dev)                            |
|--------------|--------------------------------------------|------------------------------------------|
| VS name      | `{service-name}`                           | `iot-agent-alert-api`                    |
| Host (external) | `{service-name}-{env}.iotag-{env}.com` | `iot-agent-alert-api-dev.iotag-dev.com`  |
| Destination host | `{service-name}.{namespace}.svc.cluster.local` | `iot-agent-alert-api.iotag-dev.svc.cluster.local` |
| Namespace    | `iotag-dev` or `iotag-sbx`                | `iotag-dev`                              |
| Port         | `80` (service port → container port varies)| `80`                                     |

## Common Misconfiguration Patterns

### Wrong destination host
```yaml
# WRONG — typo or wrong namespace
host: iot-agent-alert-api.iot-agent.svc.cluster.local
# CORRECT
host: iot-agent-alert-api.iotag-dev.svc.cluster.local
```

### Missing gateway reference
```yaml
# WRONG — no gateway means VS only applies inside mesh
gateways: []
# CORRECT
gateways:
  - istio-system/istio-ingressgateway
```

### Wrong port in destination
The destination port must match the **Service port** (always 80 in this project), not the container port.

### VS name conflicts
Each VirtualService name must be unique per namespace. If a VS with the same name was created by a different Helm release, it must be deleted before re-deploying:
```sh
kubectl delete virtualservice {vs-name} -n {namespace}
```

## Diagnostics

```sh
# List all VirtualServices in a namespace
kubectl get virtualservice -n {namespace}

# Analyze Istio config for issues
istioctl analyze -n {namespace}

# Check Istio proxy status for a pod
istioctl proxy-status -n {namespace}

# Check effective config for a pod
istioctl proxy-config route {pod-name}.{namespace}

# Check if the gateway is running
kubectl get pods -n istio-system -l istio=ingressgateway
```

## mTLS

- Mode: **STRICT** PeerAuthentication in all namespaces.
- All service-to-service communication is mTLS encrypted by default.
- If a service cannot reach another, check that both have Istio sidecar injected:
  ```sh
  kubectl get pods -n {namespace} -o jsonpath='{.items[*].metadata.annotations.sidecar\.istio\.io/inject}'
  ```

## DestinationRule (if needed)

Circuit breaker and retry policies are configured via DestinationRule. Currently not enabled by default. Example:
```yaml
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: {service-name}
  namespace: {namespace}
spec:
  host: {service-name}.{namespace}.svc.cluster.local
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 100
    outlierDetection:
      consecutiveErrors: 5
      interval: 30s
      baseEjectionTime: 30s
```

## Cert-Manager / TLS

TLS termination occurs at the Istio Gateway level. Certificates are managed by cert-manager via Let's Encrypt.
The Gateway resource itself (managed separately in `iot-agent-infra`) configures TLS on port 443.
VirtualServices operate on the decrypted HTTP traffic behind the gateway — do not add TLS config to VirtualServices.
