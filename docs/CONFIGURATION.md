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

Two settings decide whether a reconciliation can destroy data. Both are
fail-closed by default and both are widened in a reviewed Git change.

| Setting | Default | Effect when widened |
| --- | --- | --- |
| `ClusterProfile.spec.syncPolicy.prune` | `false` | Argo CD deletes every live object that has left the stack |
| `bootstrap/argocd-project.yaml` `sourceRepos` | in-cluster Git daemon only | the listed repository may be reconciled into the cluster |

`prune` is off because deletion is the one reconciliation outcome no rollback
undoes: a removed `StatefulSet` takes its `PersistentVolumeClaim` with it. The
`ServerSideApply=true` sync option that every rendered application carries makes
this sharper, not softer -- under server-side apply the fields Argo CD does not
own are excluded from the diff, so drift stops being reported while pruning
would keep deleting. Turn `prune` on per profile, once the destination cluster
holds nothing you cannot rebuild:

```yaml
spec:
  syncPolicy:
    prune: true
```

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
