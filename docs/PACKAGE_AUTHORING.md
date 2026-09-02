# Package authoring guide

## Prepare an immutable artifact

Choose one supported source:

| Source | Requirement |
| --- | --- |
| OCI directory | package and push an OCI artifact, record its SHA-256 digest |
| OCI Helm chart | package and push the chart, record its SHA-256 digest |
| Git directory | commit plain YAML/Kustomize, record the 40-character commit |
| Git Helm path | commit the chart directory, record the 40-character commit |
| Helm repository | publish an exact semantic version |

Floating branches, tags without a lock version, mutable image tags, and local
sibling paths are not release identities.

## Add declarative records

Create a `Package` in an external catalog:

```yaml
apiVersion: infra.convee.io/v1alpha1
kind: Package
metadata:
  name: example-app
spec:
  category: WorkloadPackage
  namespace: example-app
  source:
    type: git
    repoURL: https://example.invalid/example.git
    path: charts/example
    renderer: helm
  capabilities:
    requires: [kubernetes.api]
    provides: [example.api]
```

Select it from a `Stack` and pin it in a `VersionLock`:

```yaml
apiVersion: infra.convee.io/v1alpha1
kind: Stack
metadata:
  name: production
spec:
  packages:
    - name: example-app
      target:
        matchLabels:
          environment: production
```

```yaml
apiVersion: infra.convee.io/v1alpha1
kind: VersionLock
spec:
  packages:
    example-app:
      revision: 0000000000000000000000000000000000000000
```

## Package rules

- Do not modify Infra source to onboard an application.
- Put Secret names in values; never put Secret values in Git.
- Own health probes, PodDisruptionBudgets, telemetry resources, and migration
  hooks in the application package.
- Use `namespaceMode: values` and document namespace keys when a chart creates
  multiple namespaces.
- Keep capability names generic and stable.
- Record image digests in the VersionLock values when the artifact metadata does
  not already make them immutable.

## Validate and render

```bash
scripts/infra-python.sh scripts/infra.py validate \
  --catalog catalog/packages \
  --catalog /path/to/external/catalog \
  --stack /path/to/stack.yaml \
  --profile /path/to/profile.yaml \
  --lock /path/to/versions.lock.yaml

scripts/infra-python.sh scripts/infra.py render \
  --catalog catalog/packages \
  --catalog /path/to/external/catalog \
  --stack /path/to/stack.yaml \
  --profile /path/to/profile.yaml \
  --lock /path/to/versions.lock.yaml \
  --format applications
```

Commit the reviewed rendered result to the Git repository Argo CD reads. Infra
does not apply workload resources directly.
