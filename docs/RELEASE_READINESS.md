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
| Python dependency audit | Passed | Audited all seven exact resolved compiler dependencies with pip-audit; no known vulnerabilities reported |
| GitHub Actions | Blocked | Account billing/spending restriction prevented jobs from starting; no test steps executed at `ec29c8e`; see [core run](https://github.com/hullwork/infra/actions/runs/34243168377) and [security run](https://github.com/hullwork/infra/actions/runs/34243168268) |
| Independent Argo CD deployment | Pending | New isolated cluster; podinfo Git Helm example |
| Git update / revert / deletion retention | Pending | Must observe workload behavior and ownership, not just successful commands |
| Release archive and anonymous download | Pending | No release is published yet |
| Private vulnerability reporting | Pending | Enable and verify when the repository is public |

The first example's upstream Git commit was fetched and Helm-rendered separately:
it contains a namespaced Deployment and Service plus three Helm test Pods that
Argo CD ignores. This source check does not substitute for live sync acceptance.

## Publication procedure

1. Review all remote refs and public-facing metadata, including Actions logs and
   artifacts. A clean working tree alone is not a history audit. Rotate any live
   credential found before removing it from history.
2. Run source, dependency and security checks against the exact release commit.
3. Complete the [small application walkthrough](../examples/hello/README.md),
   including update, revert and retention, on a disposable cluster.
4. Push the reviewed commit, rerun required CI, and record the resulting run URLs.
5. Create an annotated tag matching `VERSION` and the changelog. The release
   workflow verifies ancestry, creates archives/SBOM/checksums and attests assets.
6. Verify release assets from a clean extraction. Publish as an Alpha prerelease,
   then make the repository public and verify anonymous clone/download, community
   links and the private vulnerability reporting form.

No missing runtime or CI check may be described as passed because a unit test or
workflow file exists. A Git revert of configuration does not undo a data migration.
