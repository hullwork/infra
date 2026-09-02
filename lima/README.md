# Local Lima reference

`scripts/lib/kubeadm_profile.py` is the single source of truth for the local
management and workload VM inventory. Render the checked-in definitions after
changing that file:

```bash
python3 scripts/lib/kubeadm_profile.py render --out lima
```

The management cluster runs Argo CD and the optional development Git daemon. The
workload cluster is generic capacity with an optional runtime and RWX host
capability. Neither cluster contains an application package by default.

All host routes and static addresses are reserved by the profile renderer. Do
not manually duplicate port or address literals in another file.

`tests/test_nodepool.py` compares these files against the renderer byte for
byte, so a hand edit to one of them fails the suite until the change is made in
`kubeadm_profile.py` and re-rendered.
