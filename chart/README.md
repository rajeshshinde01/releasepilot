# ReleasePilot Helm chart

This chart deploys the read-only ReleasePilot service. It deliberately does not create Kubernetes Roles, RoleBindings, deployment credentials, or secret values: ReleasePilot plans and publishes runbooks; GitHub and ArgoCD remain the approved action planes.

## Install

Build and publish the application image first, then create the secret through the approved secret-management process:

```sh
kubectl -n releasepilot create secret generic releasepilot-integrations \
  --from-literal=RELEASEPILOT_ADMIN_TOKEN='<admin-token>' \
  --from-literal=RELEASEPILOT_WORKFLOW_TOKEN='<workflow-token>'

helm upgrade --install releasepilot ./chart \
  --namespace releasepilot --create-namespace \
  --set image.repository=registry.example.internal/releasepilot \
  --set image.tag=1.0.0 \
  --set secrets.existingSecret=releasepilot-integrations
```

For a production ingress, create a small private values file outside source control:

```yaml
image:
  repository: registry.example.internal/releasepilot
  tag: "1.0.0"
ingress:
  enabled: true
  className: internal-nginx
  hosts:
    - host: releasepilot.example.internal
      paths:
        - path: /
          pathType: Prefix
secrets:
  existingSecret: releasepilot-integrations
networkPolicy:
  additionalEgress:
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: TCP
          port: 443
```

Then use `helm upgrade --install releasepilot ./chart -n releasepilot -f production-values.yaml`.

## Persistence and scaling

The default persistent volume preserves the UI runbook history and safe connection metadata across Pod restarts. Use one replica with the default `ReadWriteOnce` volume. For high availability, use a shared external database before enabling multiple replicas; this file-backed version is intentionally single-writer.

## Verify

```sh
helm lint ./chart
kubectl -n releasepilot rollout status deployment/releasepilot
kubectl -n releasepilot get pods,svc,pvc
```

When ingress is disabled, use the port-forward command printed by `helm` after installation.
