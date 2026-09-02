# Support

infra is alpha software. Support is community and best-effort; there are no
service-level objectives or production support commitments.

## What is supported

The supported project scope is the application-neutral compiler core: contract
validation, immutable version locks, release metadata import, deterministic
rendering, NodePool planning, and the generated Argo CD desired state.

For usage questions and reproducible defects, use a GitHub issue after confirming
that no existing issue covers the problem. The repository is currently private,
so only invited collaborators can open issues. This restriction will be removed
when the repository is made public.

Include:

- the infra commit or release tag;
- the command that was run and its complete non-sensitive error output;
- a minimal redacted Package, Stack, ClusterProfile, and VersionLock when relevant;
- expected and actual generated output; and
- which source, CI, Argo CD, or live-cluster checks were performed.

Do not put vulnerabilities or credentials in an issue. Follow `SECURITY.md` for
confidential security reports.

## Support boundaries

The following are not currently covered as supported production behavior:

- the experimental Lima/kubeadm management bootstrap as a production installer;
- a downstream application's behavior;
- operation, availability, or recovery of Kubernetes, Argo CD, Ceph, databases,
  registries, networks, or external secret systems;
- content and correctness of externally supplied Packages; and
- incidents caused by mutable tags, unreviewed generated output, manual changes
  outside GitOps, or unsupported old alpha revisions.

Reports about the experimental local reference are useful when clearly labelled
`local-reference`; fixes are best effort.

## Production use

The alpha compiler core can be evaluated, but it is not declared production-ready.
Production adopters must provide protected Git repositories, review and branch
controls, authenticated and highly available Argo CD, external secret management,
digest-pinned and verified artifacts, cluster security, backup and recovery, and
their own upgrade and rollback validation.
