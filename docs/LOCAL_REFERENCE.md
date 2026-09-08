# Local reference isolation

The compiler works without Lima. The optional bootstrap is a fixed experimental
lab with `infra`, `workload` and `workload-w1` VMs, a `kube` network, static addresses,
gVisor, NFS and fixed host forwards. It is not a generic cluster installer.

Before any VM/network mutation, select an absolute Lima inventory explicitly:

```bash
export INFRA_DIR="$PWD/.state"
export LIMA_HOME="$INFRA_DIR/lima"
mkdir -p "$LIMA_HOME"
make preflight
make bootstrap
make bootstrap-workload
```

Keep the same variables for subsequent bootstrap, NodePool operations and
`make destroy`. The scripts refuse mutations when `LIMA_HOME` is absent or
relative. They do not implicitly adopt the user's global inventory or move VMs.
`make destroy` stops VMs and retains their disks.

Separate inventories isolate VM names and network state, but **not host ports**.
Run one copy of this fixed topology on a host at a time. Preflight must report
occupied ports; do not bypass that check to run a second instance. Custom cluster
layouts belong to an external provider adapter or your own Kubernetes setup,
not to the portable Package/Stack/Profile/Lock contract.

For an existing reference installed in the default Lima inventory, first inspect
`limactl list`, confirm the exact VM names belong to this installation, then set
`LIMA_HOME` to that inventory's absolute path and keep the original `INFRA_DIR`.
Setting a new path creates a separate inventory; it is not a migration. This
change never deletes, moves or automatically imports existing VM disks.
