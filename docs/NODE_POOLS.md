# Node pools and capacity

A `NodePool` separates capacity intent from workload packages. Applications
request ordinary Kubernetes scheduling and do not know whether nodes come from
the local provider, Cluster Autoscaler, Karpenter, or an external system.

```text
NodePool declaration
        │
        ├── validate ---- fail closed before mutation
        ├── plan -------- staged, reviewable operation
        └── apply ------- explicit provider mutation
                         │
                         ├── bounded JSONL events
                         └── atomic Prometheus textfile metrics
```

Inspect the local declaration without changing a cluster:

```bash
make validate-nodepool
make plan-nodepool
```

Apply is explicit:

```bash
scripts/infra-python.sh scripts/nodepool.py apply \
  --pool examples/node-pools/local-workload.yaml \
  --desired 1
```

## Local provider safety

The local adapter reconciles only the finite worker inventory declared in the
NodePool. Scale-up waits for node readiness, CNI, any declared runtime
dependencies, and workload convergence. Scale-down requires an idle worker and
fails closed on active non-DaemonSet Pods, node-local persistent volumes, and
unsafe `emptyDir` data. It retains the provider disk.

Dynamic workers carry stable pool labels and a `NoSchedule` taint. A worker must
not mix system and dynamic workloads; migrate mixed-use workloads before
scaling down.

Every reconcile records `preflight`, `capacity-change`, `node-ready`,
`cni-runtime-ready`, `workload-convergence`, and `verification` events with
duration and terminal status. The active log rotates at 5 MiB and metrics are
atomically replaced.

## What `spec.observability` actually does

| Field | Effect |
| --- | --- |
| `eventLog` | Path of the JSONL event log. Relative paths resolve against the repository root. |
| `phaseTimeoutSeconds` | Ceiling on one `apply`, in wall clock. The phases run inside the adapter, so this bounds the whole run rather than each phase. On expiry the adapter's entire process group is ended, a terminal `failed` event is appended, the metrics file is rewritten, and `apply` exits 2. |
| `metricsPrefix` | Metric name prefix, default `infra_nodepool`. Renames `<prefix>_reconcile_failed` and `<prefix>_phase_duration_seconds`. |

The process group matters: an adapter typically runs `limactl` and `kubectl`
children, and ending only the adapter leaves a half-finished VM operation
running after this tool has already reported the reconcile as failed.

## Cloud provider boundary

- Cluster Autoscaler adapters own cloud node-group propagation.
- Karpenter adapters own cloud NodeClass and provider-specific resources.
- External provider commands receive `reconcile --pool FILE --desired N` and
  must return non-zero on partial or unsafe operations.

Credentials, subnet discovery, machine images, disruption budgets, and workload
identity belong to the provider environment, not NodePool YAML.
