# Configuration ownership

| Configuration | Authority | Admin surface |
| --- | --- | --- |
| Package selection and placement | reviewed Stack | Read-only or reviewed change |
| Namespace and Helm values | reviewed Stack and package defaults | Reviewed change with diff |
| Artifact identity | Package plus VersionLock | Import/status only |
| Cluster list, labels, capabilities | reviewed ClusterProfile | Reviewed change |
| Worker min/max/desired and drain policy | reviewed NodePool | Plan, diff, approval, audit |
| Provider topology and credentials | target provider or secret manager | Operator-only |
| Application feature flags and quotas | owning application | Application admin |
| Telemetry retention and alert routes | owning package or platform addon | Reviewed Git change |

Infra has no tenant-facing administrative backend. A future console should be a
GitOps client: validate contracts, render a plan, open a reviewed change, and
display reconciliation status. It must not call `kubectl apply` for workload
state or store secret values.

## Deletion policy and the Argo CD project

The compiler separates the control namespace and three deletion decisions:

```yaml
spec:
  argocdNamespace: argocd  # use the namespace of your Argo CD installation
  syncPolicy:
    automated: true
    prune: false
    selfHeal: true
  applicationSetPolicy:
    applicationsSync: create-update
    preserveResourcesOnDeletion: true
```

- `prune` governs removal of workload resources during Application sync.
- `applicationsSync` governs creation/update/deletion of generated Applications.
  Argo CD's controller policy takes precedence unless per-set overrides are
  enabled (`applicationsetcontroller.enable.policy.override: "true"` in
  `argocd-cmd-params-cm`, followed by a controller restart). Have your operator
  configure this; local bootstrap does so automatically.
- `preserveResourcesOnDeletion` prevents the controller from adding a workload
  deletion finalizer to new Applications. It does not prevent the ApplicationSet
  itself from being deleted, and is independent of `prune`.

For existing Applications, inspect `metadata.finalizers` before migration: a
previously installed deletion finalizer can still remove workloads. Resolve those
finalizers through a reviewed migration before deleting the parent. Test removal
on disposable resources first. See the upstream
[ApplicationSet deletion semantics](https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Application-Deletion/)
and [controller policies](https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Controlling-Resource-Modification/).

The local Git publisher's parent Application has `prune: false` and no deletion
finalizer. Git changes to retained children reconcile automatically; removing a
child from the directory leaves that control resource present until an explicit
retirement. Preserve or back up data, stop the owning reconciliation, then remove
reviewed resources deliberately. `helm`/Kubernetes data-retention behavior belongs
to each package. `ServerSideApply=true` controls field ownership, not backup or deletion.

`bootstrap/argocd-project.yaml` replaces the permissive `AppProject` that Argo CD
installs under the name `default`. A package sourced from an external Helm or
OCI registry is refused until that registry is listed, with:

```
application repo <repoURL> is not permitted in project default
```

Add the exact repository, never a wildcard, and keep the enumeration reviewable:

```yaml
spec:
  sourceRepos:
    - git://git-server.gitops-system.svc.cluster.local/gitops.git
    - oci://ghcr.io/your-org/your-packages
```

The same applies to `destinations` when a `ClusterProfile` adds a cluster, and to
`clusterResourceWhitelist` when a package needs a cluster-scoped kind that is not
already listed. A `"*"` entry on any of those three axes disables that axis; the
regression test in `tests/test_argocd_guardrails.py` fails if one appears.
