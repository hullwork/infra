# Open-source scope and maturity

## Supported Alpha surface

The supported surface is application-neutral:

- `infra.convee.io/v1alpha1` contracts;
- deterministic Argo CD rendering;
- immutable OCI, Git, and Helm version locks;
- external catalog composition;
- release metadata import;
- NodePool validation and planning; and
- fresh-clone validation.

## Experimental local reference

The Lima/kubeadm management bootstrap, local Git daemon, and finite local
worker inventory are experimental development fixtures with fixed addresses/ports.
Mutation requires an explicit absolute `LIMA_HOME` so another inventory is never
adopted implicitly. See [Local reference isolation](LOCAL_REFERENCE.md).
These fixtures do not define the package API and are not promised as a production installer.

## Not a hosted service or security boundary

Infra compiles reviewed desired state and provides local capacity tooling. It
is not a controller, secret manager, certificate authority, cluster security
boundary, or managed service. Operators remain responsible for hardening,
credentials, network policy, backups, upgrades, and recovery testing.
