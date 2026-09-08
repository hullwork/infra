# Alpha release readiness

This document records release evidence, not a production certification. The
candidate is `0.1.0-alpha.1`. Only results actually executed are marked passed.

| Gate | Status | Evidence / limit |
| --- | --- | --- |
| Unit tests and fresh-clone install | Passed | macOS: 114 tests and clean-clone install/validate/render at `ec29c8e`; later workflow changes passed their focused checks |
| Python 3.10 / 3.12 | Passed | CPython 3.10.16 and 3.12.9, 114 tests each |
| Linux source verification | Passed | Alpine 3.20 / Python 3.12.13: 114 tests and hello validation, with Bash, Git and Make installed; this does not replace the GitHub Ubuntu runner |
| Full remote Git history secret scan | Passed | Gitleaks 8.30.1, all refs: 21 reachable commits (14 non-merge commits scanned), no findings; candidate changes separately scanned on 2026-09-08 |
| Public-facing metadata | Passed | Reviewed 48 historical Actions runs, downloaded all 43 runs with steps, scanned 5.39 MB of logs and issue/PR text; no secret findings; no uploaded Actions artifacts existed |
| Python dependency audit | Passed | Audited all seven exact resolved compiler dependencies in both Python 3.10 and 3.12 environments with pip-audit; no known vulnerabilities reported |
| GitHub Actions | Blocked | Account billing/spending restriction prevented jobs from starting; no test steps executed at `ec29c8e`; see [core run](https://github.com/hullwork/infra/actions/runs/34243168377) and [security run](https://github.com/hullwork/infra/actions/runs/34243168268) |
| Independent Argo CD deployment | Passed | Argo CD 3.5.2 in dedicated namespaces on Docker Desktop Kubernetes 1.32.2, arm64; podinfo Git Helm example reached Synced/Healthy |
| Git update / revert / deletion retention | Passed | HTTP content changed and reverted; deleting the ApplicationSet garbage-collected its child Application while retaining the Deployment, Service and HTTP response |
| Browser acceptance | Passed | Chrome showed `Hello again`, PING incremented, and a reload after revert/retirement showed `Hello from Infra` with version 6.15.0 |
| Release archive and anonymous download | Pending | No release is published yet |
| Private vulnerability reporting | Pending | Enable and verify when the repository is public |

The first example's upstream Git commit was fetched and Helm-rendered separately:
it contains a namespaced Deployment and Service plus three Helm test Pods that
Argo CD ignores. This source check does not substitute for live sync acceptance.

## Runtime acceptance details

The acceptance used a separate temporary Git repository served over Smart HTTP,
an existing local Kubernetes cluster with no previous application namespaces, and
new `argocd` / `infra-hello` namespaces. No local-reference bootstrap or companion
repository was used. Upstream chart content was fetched directly from GitHub at
`dd507173b7b75b2312a36cabe0de5f09c1ce69c8` (podinfo 6.15.0).

- Initial Git revision `f003de8`: parent created `hello-podinfo`; its child
  `local-hello-podinfo` synced and served `Hello from Infra`.
- Revision `869dada`: changed catalog values were recompiled and committed;
  the browser and HTTP API showed `Hello again` after child sync.
- Git revert `e19445d`: child sync restored `Hello from Infra`.
- Revision `22289ec`: removed the ApplicationSet definition while retaining a
  harmless marker ConfigMap. With parent pruning disabled, the ApplicationSet
  remained and the parent reported OutOfSync / `requiresPruning` as expected.
- Explicitly deleting that retained ApplicationSet removed its child Application;
  the workload still served the reverted response. The parent then became Synced.

Images were downloaded from their upstream registries through the host and loaded
into Docker after direct pulls timed out; the disposable Argo CD installation used
`IfNotPresent`. This proves reconciliation with preloaded images, not a clean
registry-pull path. Kubernetes 1.32 is outside Argo CD 3.5's upstream tested matrix
(1.33–1.36), so this smoke run is not a production compatibility recommendation.
OCI source pulls, separate remote clusters, data recovery and the Lima bootstrap
were not exercised. Repeat acceptance on your supported production versions.

## Publication procedure

1. Review all remote refs and public-facing metadata, including Actions logs and
   artifacts. A clean working tree alone is not a history audit. Rotate any live
   credential found before removing it from history.
2. Run source, dependency and security checks against the exact release commit.
3. Complete the [small application walkthrough](../examples/hello/README.md),
   including update, revert and retention, on a disposable cluster.
4. Push the reviewed commit. After local acceptance and public-content review,
   make the repository public, enable private vulnerability reporting, and verify
   anonymous clone and community links. GitHub Free/Pro/Team plans require a public
   repository for [artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
5. Rerun required CI on the exact candidate and record the resulting run URLs.
   Do not publish a version while required jobs are blocked or failing.
6. Create an annotated tag matching `VERSION` and the changelog. The release
   workflow verifies ancestry, creates archives/SBOM/checksums and attests assets.
7. Verify the Alpha prerelease assets by anonymous download, checksums, attestation
   verification and a clean extraction. Source visibility and version publication
   are separate milestones; a public repository alone is not a published release.

No missing runtime or CI check may be described as passed because a unit test or
workflow file exists. A Git revert of configuration does not undo a data migration.
