# Plugin and package architecture

The normal onboarding path is declarative and requires no Infra code change:

1. publish an immutable OCI artifact, Git tree, or versioned Helm chart;
2. add a `Package` descriptor to an external or local catalog;
3. select it from a reviewed `Stack`;
4. pin its exact digest, commit, or version in `VersionLock`;
5. validate and render; then commit the reviewed GitOps result.

## Contracts

- `Package` describes source, namespace mode, capabilities, and default values.
- `Stack` selects packages and supplies placement and value overrides.
- `ClusterProfile` lists destinations, labels, capabilities, project, and sync
  policy.
- `VersionLock` is the only artifact identity authority.
- `NodePool` declares bounded worker capacity and safe scale-down policy.
- `IntegrationPlugin` is an optional external executable ABI for a capability
  that cannot be represented as an immutable package.

OCI and Git directory packages cannot receive Helm values. OCI and Git Helm
packages can. A missing source-specific lock field is a hard error.

## External IntegrationPlugin

An IntegrationPlugin descriptor and entrypoint are supplied from outside this
repository. It must declare `doctor`, `up`, `status`, and `verify`; consume
explicit inputs; return machine-readable progress where practical; and never
modify Infra source. Infra can discover the descriptor but does not bundle an
application adapter.

Do not create an IntegrationPlugin when a Package is sufficient. In particular,
an adapter that only invokes Helm or Kustomize for a released application is a
misuse of the extension point.

## Reconciliation limits

Capability checks are compile-time checks, not runtime health orchestration.
Packages requiring strict startup order must expose health checks and be
sequenced through separately reviewed bootstrap, platform, and workload Git
changes.
