# Architecture review

## Core and extension boundary

| Area | Current boundary | Assessment |
| --- | --- | --- |
| Compiler | `scripts/infra.py` renders immutable Package/Stack/Profile/Lock inputs | Strong |
| Contracts | JSON Schema rejects unknown fields and floating identities | Strong |
| External catalogs | multiple catalogs can be composed without code changes | Strong |
| Capacity | NodePool is separate from workload packages | Strong |
| Local bootstrap | management plane only; Installs no application package | Improved |
| Extension | IntegrationPlugin descriptors must be external | Improved |

## Independent package boundaries

Infra does not bundle application manifests, source checkouts, deployment
callbacks, credentials, or application lifecycle scripts. An application is a
versioned artifact plus external catalog records. Its chart owns health,
telemetry, migrations, and recovery semantics.

## Robustness

- Exact OCI digest, Git commit, or Helm version is mandatory.
- Package capabilities are checked per matching cluster.
- Multi-namespace packages cannot use a misleading destination override.
- NodePool plans are reviewable and scale-down is fail-closed.
- Fresh-clone rendering is a repository test.
- Runtime Argo reconciliation and real provider failure drills are still not
  recorded and therefore remain unscored.

## Decoupling

The tracked compiler, provider core, examples, and bootstrap contain no
first-party application name. Historical application-specific GitOps trees and
adapters were removed rather than carried as compatibility paths.

## i18n

The compiler emits language-neutral Kubernetes and Argo CD resources. Operator
documentation is English; application-facing language and locale behavior belong
to application packages.

## Code simplicity

The source tree separates a small deterministic compiler, NodePool planner, and
local bootstrap. Complex orchestration was removed from the core rather than
hidden behind conditionally unused shell functions.

## Newcomer onboarding

The supported path is `make validate`, `make render`, `make render-applications`,
and `make fresh-clone-test`. Adding an unrelated package requires catalog data
and a lock, not repository code.

## Unfinished work

- Publish a real rendered GitOps repository and verify Argo reconciliation.
- Execute the full NodePool scale-up and fail-closed drain matrix.
- Add a cloud provider package and integration fixture.
- Measure deterministic render and compiler performance.
