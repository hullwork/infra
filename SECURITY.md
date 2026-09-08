# Security Policy

## Project status and scope

Infra is alpha software. The supported security scope is the application-neutral
compiler surface:

- schemas in `contracts/v1alpha1`;
- deterministic Package, Stack, ClusterProfile, and VersionLock rendering;
- immutable release metadata import;
- NodePool validation and provider plan generation; and
- the optional local management-cluster bootstrap.

The compiler produces desired state but does not deploy it. Operators remain
responsible for reviewing generated resources, protecting Git and Argo CD,
managing secrets, configuring cluster access, and securing Kubernetes and its
storage.

The local Lima/kubeadm reference and in-cluster Git daemon are development
fixtures. They are not hardened production services and must not be exposed
beyond an isolated lab network.

## Supported versions

Before the first stable release, fixes are provided on a best-effort basis for
the latest commit on `main` and the latest tagged `v0.x` release.

| Version | Supported |
| --- | --- |
| Latest `main` / latest tagged `v0.x` | Best effort |
| Older alpha revisions | No |

## Reporting a vulnerability

Do not report suspected vulnerabilities in a public issue, discussion, pull
request, or chat room. Use this repository's private Security advisory
workflow at <https://github.com/hullwork/infra/security/advisories/new>.
If the private form is unavailable, use the "Request a private contact channel"
issue template, with no vulnerability details. A maintainer will arrange a
confidential channel before requesting any details.

Include the affected revision, impact, reproduction conditions, exploit status,
and a suggested mitigation. Maintainers will acknowledge reports on a
best-effort basis within five business days.

Changes involving artifact identity, digest validation, provider commands,
generated Argo CD permissions, cluster credentials, secret references, or remote
source handling require focused tests and reviewer attention. Never commit
credentials, kubeconfig contents, private keys, access tokens, or ignored local
state.
