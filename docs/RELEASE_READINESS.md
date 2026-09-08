# Alpha release readiness

This document records release evidence, not a production certification. The
candidate is `0.1.0-alpha.1`. Only results actually executed are marked passed.

| Gate | Status | Evidence / limit |
| --- | --- | --- |
| Unit tests and fresh-clone install | Pending | Run against the final candidate commit |
| Python 3.10 / 3.12 | Pending | Run both minimum and reference interpreters |
| Full remote Git history secret scan | Passed | Gitleaks 8.30.1, all remote refs, 14 commits, no findings on 2026-09-08; candidate additions require a final scan |
| Python dependency audit | Pending | Audit dependencies installed for the candidate |
| GitHub Actions | Blocked | Account billing/spending restriction prevented jobs from starting; no test steps executed |
| Independent Argo CD deployment | Pending | New isolated cluster; podinfo Git Helm example |
| Git update / revert / deletion retention | Pending | Must observe workload behavior and ownership, not just successful commands |
| Release archive and anonymous download | Pending | No release is published yet |
| Private vulnerability reporting | Pending | Enable and verify when the repository is public |

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
