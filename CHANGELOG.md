# Changelog

All notable changes to Infra are documented in this file. Releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (breaking)

- `ClusterProfile.spec.syncPolicy.prune` now defaults to `false` instead of
  `true`, and the effective value is written into every rendered application
  rather than left to an Argo CD default. Deleting a live object is the one
  reconciliation outcome no rollback undoes, so automated deletion is now
  opt-in. Set `prune: true` in the profile to restore the previous behaviour.
- `bootstrap/argocd-project.yaml` replaces Argo CD's permissive `default`
  `AppProject` with enumerated lists: `sourceRepos` holds only the in-cluster
  Git daemon, `destinations` are listed per cluster, and
  `clusterResourceWhitelist` names ten kinds with no wildcard. A package whose
  source is an external Helm or OCI registry is now rejected with
  `application repo ... is not permitted in project default` until that exact
  registry is added. The project keeps the name `default` on purpose, because
  `ClusterProfile.spec.project` falls back to it. The namespace axis stays `"*"`
  because package namespaces are declared per catalog and are not knowable at
  bootstrap time. See `docs/CONFIGURATION.md` for the widening procedure.

### Removed

- `images/s3-client/` and `storage/rook/`. Neither was reachable from any
  script, Makefile target, workflow step or document; the only occurrences of
  their names anywhere else were two test witness entries. `images/s3-client`
  additionally built a patched MinIO Client (AGPL-3.0) that nothing here
  consumed. The release asset allowlist drops `images` and `storage` to match,
  so a release archive no longer ships them.

### Changed

- Removed the bundled first-party application GitOps trees and lifecycle
  adapters; application composition now uses external Package catalogs.
- Narrowed the local bootstrap to the product-neutral management plane.
- Replaced the local four-application topology with generic management and
  workload reference clusters.
- Added Git Helm package rendering and multiple external catalog composition.
- Made local NodePool policy reconcile stale managed labels and taints.
- Rebuilt the public documentation around the immutable package boundary.

### Fixed

- `NodePool.spec.observability.phaseTimeoutSeconds` is enforced. It was
  required by the schema and shipped in the example, and nothing read it: a
  provider adapter that hung hung `nodepool.py apply` forever, and the terminal
  `failed` event that both the JSONL log and `infra_nodepool_reconcile_failed`
  are built around was never written. The adapter now runs in its own process
  group and the whole group is ended at the declared ceiling, so a killed
  reconcile does not leave `limactl`/`kubectl` children running unattended.
- `NodePool.spec.observability.metricsPrefix` is honoured. It was declared and
  ignored, so a dashboard built on the name an operator wrote found no series.
  `phase` and `status` are now Prometheus-escaped like every other label.
- The first-party-name gate scanned whatever `tracked_files()` returned and
  then checked that count against the same call, which is true by
  construction. Narrowing the scan face to `*.md` dropped it from 79 files to
  16 and the suite stayed green -- the exact suffix-allowlist regression its
  own comment warns about. It now pins witnesses and a floor, the way the
  sibling gate in `test_project_naming.py` already did.
- The release-archive completeness test no longer fails after the README quick
  start creates `.venv/`. It derived required archive members from paths that
  exist on disk, which included that gitignored directory; it now derives them
  from git-tracked paths, since `git archive` can carry nothing else.

## [0.1.0] - 2026-09-01

### Added

- Product-neutral Package, Stack, ClusterProfile, and VersionLock contracts.
- Fail-closed compilation of versioned packages into Argo CD desired state.
- Immutable OCI package and runtime-image release imports.
- Standalone validation, rendering, and fresh-clone verification.

[Unreleased]: https://github.com/hullwork/infra/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hullwork/infra/releases/tag/v0.1.0
