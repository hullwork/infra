# Compatibility

Infra is alpha. The table distinguishes a supported input or implementation
requirement from evidence that a particular combination has been executed.

| Surface | Requirement / policy | Verification |
| --- | --- | --- |
| Compiler | Python 3.10+; PyYAML 6.x; jsonschema 4.x | CI covers Python 3.10 and 3.12 on Linux and macOS; see release readiness for actual run results |
| Shell helpers | Bash 3.2+ on macOS; Bash on Linux | Source suite includes macOS system Bash error/lock cleanup tests |
| Contract | `infra.convee.io/v1alpha1` | Unknown fields and invalid combinations fail validation; alpha changes require migration notes |
| Argo CD output | Application and ApplicationSet APIs; `helm.valuesObject` and per-set deletion controls | Smoke-tested with Argo CD 3.5.2; earlier versions are not claimed supported |
| OCI directory / OCI Helm artifact | Argo CD native OCI source support, a supported media type and immutable digest | Compiler output is tested; live OCI pull is not covered by the Git Helm smoke test |
| Kubernetes | A version supported by the chosen Argo CD release and workload | Local smoke test used 1.32.2, outside Argo CD 3.5's upstream tested 1.33–1.36 matrix; a production version matrix is not yet verified |
| Local Lima/kubeadm reference | macOS, explicit absolute `LIMA_HOME`, fixed topology and ports | Experimental; independent GitOps acceptance does not validate this bootstrap |

Use the latest appropriate security patch of your chosen Argo CD/Kubernetes
release line and repeat acceptance before upgrades. The baseline above is not a
promise that every later release is compatible. See the upstream
[Argo CD installation and tested versions](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)
and [OCI source documentation](https://argo-cd.readthedocs.io/en/stable/user-guide/oci/).

## Deletion prerequisites

`applicationsSync: create-update` requires Argo CD's per-ApplicationSet policy
override to be enabled and not superseded by a global controller policy.
`preserveResourcesOnDeletion: true` is separate. Existing Application finalizers
need inspection during migration. See [configuration ownership](CONFIGURATION.md).

## Reproducibility boundaries

Equal compiler inputs produce equal Argo CD YAML. That does not by itself prove
that an external registry served the same bytes or that a chart's image tags are
immutable. Git commits pin chart/source content; OCI digests pin OCI artifacts;
an exact Helm repository version still depends on upstream version immutability.
For runtime image byte identity, lock image digest values supported by the chart.
Dependency ranges in `requirements-dev.txt` are intentional compatibility ranges;
record installed versions with acceptance evidence.
