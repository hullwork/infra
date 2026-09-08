# infra — a GitOps compiler for versioned Kubernetes packages

`infra` compiles four reviewed records into Argo CD desired state.

The compiler, `scripts/infra.py`, is a pure function of its inputs. It has no
controller and no daemon; it opens no network connection, reads no environment
variable, and never contacts a cluster. The same inputs produce byte-identical
YAML, and nothing reaches a cluster until a human commits the output.

```text
Package catalog + Stack + ClusterProfile + VersionLock
                 │
                 ▼   scripts/infra.py   (offline, deterministic)
       Argo ApplicationSet / Application YAML
                 │
                 ▼   reviewed Git change
           Argo CD reconciliation
```

| Record | Answers |
| --- | --- |
| `Package` | where an immutable artifact lives, which namespace it owns, which capabilities it needs and offers |
| `Stack` | which packages are selected, where they land, which values are overridden |
| `ClusterProfile` | which clusters exist, what they already provide, what reconciliation may do to them |
| `VersionLock` | the exact digest, commit, or version — the only artifact identity authority |

Each record is validated against a JSON Schema under `contracts/v1alpha1/` that
rejects unknown fields, so a misspelled key fails the build instead of being
silently ignored.

The repository is deliberately application-neutral. It contains no first-party
application manifest, source checkout requirement, deployment callback, or
product-specific shell branch. Applications publish immutable OCI, Helm, or Git
artifacts and enter through external catalog data.

> **Use it independently.** You need this repository and your package records.
> Follow [Use an existing Argo CD installation](docs/EXISTING_ARGOCD.md) to go
> from rendered output to a watched Git directory. No companion repository or
> Lima management cluster is required for that path.

Start with [a small working application](examples/hello/README.md) to deploy,
change and revert a namespaced HTTP service. Check [compatibility](docs/COMPATIBILITY.md)
and [release readiness](docs/RELEASE_READINESS.md) for the tested scope.

## When to use Infra

Use Infra when a platform team needs to compose independently owned package
catalogs and reject invalid capability combinations or artifact references in CI
before generating Argo CD configuration. Capability checks are static: they do
not wait for services, verify live cluster features or order data migrations.

If your only requirement is to map applications to clusters, start with native
[ApplicationSet generators](https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Generators/).
If your workflow centers on Helm releases, compare [Helmfile](https://github.com/helmfile/helmfile),
which already supplies environments, dependency ordering and chart lock files.
Infra adds a separate package contract and per-cluster capability validation;
it delegates rendering of package contents and continuous deployment to Argo CD.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

make help                 # every target with its one-line description
make validate             # contracts, capability graph, and locks
make render               # ApplicationSet stream on stdout
make render-applications  # plain Application stream, easier to review
make test                 # the unit suite
make fresh-clone-test     # clone, install, validate and render in a clean tree
```

Every target runs through `scripts/infra-python.sh`, which prefers
`.venv/bin/python`, falls back to `uv run`, and finally to `python3`. Once the
virtual environment exists, no `PYTHON=` export is needed.

The default example is one demo OCI Helm package with a placeholder digest; it
tests rendering and cannot be installed. Each input is a `make`
variable, so the same targets work on your own records:

```bash
make render \
  CATALOG=examples/third-party/catalog \
  STACK=examples/third-party/stack.yaml \
  PROFILE=examples/third-party/profile.yaml \
  LOCK=examples/third-party/versions.lock.yaml
```

## What this buys you over running Helm by hand

**Onboarding a package is three data records and no Infra code change.** A
`Package` descriptor, one line in a `Stack`, and one entry in a `VersionLock`.
There is no adapter to write, no branch in a shell script, and no place in the
compiler that knows your application's name.

**Locks are bound to the source type, and unused fields are rejected.** An OCI
package requires a `sha256:` digest and may not carry a Git `revision`; a Git
package requires a 40-character commit and may not carry a `version` or
`digest`; a Helm repository package requires an exact semantic version and may
not carry either. A lock that merely looks pinned fails validation instead of
rendering a floating reference.

**Capabilities are solved per cluster, not globally.** For every cluster in the
profile, the compiler builds a dependency graph from the packages actually
placed on it and then refuses three distinct shapes: a requirement nothing
provides, a requirement that two selected packages both provide (an ambiguity
is reported, not resolved by a coin flip), and a dependency cycle, which is
printed as the cycle path.

**The neutrality claim is a test, not a promise.**
`tests/test_documentation.py` scans every git-tracked file — including
extension-less ones such as `Makefile` and `Dockerfile` — for retired
first-party product names, and `tests/test_project_naming.py` does the same for
the retired project prefix. Both gates pin their matchers in two directions,
against samples they must catch and samples they must not, so a pattern that has
been narrowed until it guards nothing fails the suite instead of passing
quietly.

**A real upstream chart is the proof, not a fixture.**
[`examples/third-party`](examples/third-party/README.md) composes cert-manager
directly from `https://charts.jetstack.io`, an upstream chart with no
relationship to this repository, using only catalog data.
`tests/test_gitops_plugins.py` validates and renders it on every run and asserts
the resulting `repoURL`, `chart`, and `targetRevision`.

## Compose external catalogs

The compiler accepts `--catalog` more than once, so application teams can own
their `Package` descriptors while an operator composes only the selected set:

```bash
scripts/infra-python.sh scripts/infra.py validate \
  --catalog catalog/packages \
  --catalog /path/to/external/catalog \
  --stack /path/to/stack.yaml \
  --profile /path/to/profile.yaml \
  --lock /path/to/versions.lock.yaml
```

Supported immutable sources:

| Source | Lock authority | Helm values |
| --- | --- | --- |
| OCI directory | SHA-256 digest | No |
| OCI Helm artifact | SHA-256 digest | Yes |
| Git directory | 40-character commit | No |
| Git Helm path | 40-character commit | Yes |
| Helm repository | Exact semantic version | Yes |

Secret values are intentionally absent. Packages name external Secret resources;
creation and rotation belong to the target environment.

Write the reviewed stream to a file rather than redirecting stdout when the
result is going into Git:

```bash
scripts/infra-python.sh scripts/infra.py render \
  --catalog catalog/packages \
  --stack examples/stacks/demo.yaml \
  --profile examples/profiles/local.yaml \
  --lock versions.lock.yaml \
  --format applications \
  --output /tmp/infra-applications.yaml
```

See [Package authoring](docs/PACKAGE_AUTHORING.md) for the full record reference.

## Defaults that fail closed

Deletion has three separate controls. Changing one does not change the others:

| Profile setting | Default | Controls |
| --- | --- | --- |
| `spec.syncPolicy.prune` | `false` | Removing workload resources during an Application sync |
| `spec.applicationSetPolicy.applicationsSync` | `create-update` | Whether ApplicationSet may delete generated Applications |
| `spec.applicationSetPolicy.preserveResourcesOnDeletion` | `true` | Retaining workload resources when generated Applications are deleted |

Argo CD must allow per-ApplicationSet policy overrides for `applicationsSync` to
be honored. The local bootstrap enables that setting; an existing installation's
operator must configure it. `preserveResourcesOnDeletion` protects resources
separately. It does not prohibit a direct deletion by a cluster administrator.
Existing Applications may retain deletion finalizers: inspect them before relying
on new preservation defaults. See [Configuration ownership](docs/CONFIGURATION.md).

The renderer keeps `ServerSideApply=true` for field ownership. Server-side apply
is not a deletion or backup policy. StatefulSet PVC retention depends on its
retention policy; deleting a StatefulSet does not universally delete its PVCs.

`bootstrap/argocd-project.yaml` replaces the fully permissive `AppProject` that
Argo CD installs under the name `default`. It keeps that name on purpose,
because `ClusterProfile.spec.project` falls back to it: a profile that omits the
field must land in a narrow project, not a wildcard one. `sourceRepos`,
`destinations`, and `clusterResourceWhitelist` are enumerated line by line, so a
package sourced from an external Helm or OCI registry is refused with

```text
application repo <repoURL> is not permitted in project default
```

until that exact registry is listed. The namespace axis stays `"*"`: package
namespaces are declared per catalog and are not knowable at bootstrap time.
`tests/test_argocd_guardrails.py` fails if a `"*"` appears on any of the other
three axes. The widening procedure is in
[Configuration ownership](docs/CONFIGURATION.md).

## Local management reference

The optional local bootstrap creates a Lima/kubeadm management cluster, an
in-cluster Git daemon, and Argo CD. It installs no workload package.

```bash
export LIMA_HOME="$PWD/.state/lima"
mkdir -p "$LIMA_HOME"
make preflight
make bootstrap
make bootstrap-workload
```

Read [Local reference isolation](docs/LOCAL_REFERENCE.md) before using an existing
Lima inventory. VM names, addresses and host ports remain a fixed experimental
fixture; this is not a multi-installation or production cluster provisioner.

`make bootstrap` and `make publish-rendered` refuse to run unless the tree is a
clean checkout of `main` that exactly matches `origin/main`, or an unpacked
release archive. On any other branch they stop with `release blocked: source
branch must be main`.

The Git server is a bare repository persisted by a PVC. Rendered Applications
can be published to it for the local lab:

```bash
make publish-rendered RENDERED_NAME=demo \
  RENDERED_FILE=/tmp/infra-applications.yaml
```

The publisher accepts only Argo `Application` or `ApplicationSet` resources.
It registers one parent Application watching `rendered/<name>` on Git `main`;
subsequent commits and reverts are reconciled from that directory. Root pruning
is disabled, so retiring a child definition requires a separate reviewed removal.
It applies no workload directly. Production should use a protected external Git
provider or package registry. The unauthenticated `git://` daemon is not a
production security boundary.

`make destroy` stops the reference VMs while retaining disks and local ignored
state under `.state/`.

## Node pools

A `NodePool` expresses bounded worker capacity independently of workload
packages:

```bash
make validate-nodepool NODEPOOL=examples/node-pools/local-workload.yaml
make plan-nodepool NODEPOOL=examples/node-pools/local-workload.yaml
```

Both are read-only; `plan` prints a staged `NodePoolPlan` and mutates nothing.
The built-in local adapter reconciles only its declared finite worker inventory,
and scale-down is fail-closed: active pods, local persistent data, and unsafe
`emptyDir` use block the operation. See [Node pools](docs/NODE_POOLS.md).

## What is verified, and what is not

Read this before trusting a green build.

**CI never starts a VM.** `.github/workflows/plugin-core-verify.yaml` runs
`make test`, `make validate`, `make render`, and `make fresh-clone-test`. It
never runs `make preflight`, `make bootstrap`, or `make bootstrap-workload`, and
it never creates a Lima VM or a cluster. A green run therefore says nothing
about the local bootstrap path. That gap has already cost real time: the VM
definitions once pointed at a `pkgs.k8s.io/management:` package repository that
does not exist, and the suite stayed green until someone booted a VM by hand.
`tests/test_nodepool.py` now pins the real upstream package repository URL in
all three `lima/*.yaml` files and in the renderer that produces them.

**Shell verification has limits.** The unit suite checks shell syntax and lock
cleanup, including macOS Bash 3.2, and exercises publication against a temporary
Git remote with a simulated cluster. CI does not run ShellCheck. These checks
do not establish VM or Argo CD runtime correctness.

**`IntegrationPlugin` is a contract with no implementation.** The schema
(`contracts/v1alpha1/integration-plugin.schema.json`) and a loader
(`scripts/plugin.py`) exist. Nothing in this repository ships a plugin or calls
the loader: no Makefile target, no script, no workflow, and no test. Treat it as
a reserved extension point, and prefer a `Package` — see
[Plugin architecture](docs/PLUGIN_ARCHITECTURE.md).

**Runtime reconciliation is unmeasured.** No Argo CD sync, drift-recovery, or
NodePool scale drill has been recorded, and no render performance numbers exist.
The fail-closed [benchmark template](docs/BENCHMARK_REPORT_TEMPLATE.md) keeps
those scenarios marked `NOT RUN` rather than converting them into scores.

## Boundaries

- Infra owns contracts, deterministic rendering, version locks, and local
  capacity plumbing.
- Argo CD owns reconciliation after the rendered Git change is reviewed.
- Package owners own charts, health, telemetry, secrets contracts, data
  migrations, and application recovery.
- Cluster operators own credentials, network policy, storage, backup, and
  production access.

Infra is not a controller, secret manager, certificate authority, cluster
security boundary, or managed service. See
[Open-source scope](docs/OPEN_SOURCE_SCOPE.md) for the supported surface and the
experimental one.

## Further reading

[Package authoring](docs/PACKAGE_AUTHORING.md) ·
[Plugin architecture](docs/PLUGIN_ARCHITECTURE.md) ·
[Configuration ownership](docs/CONFIGURATION.md) ·
[Node pools](docs/NODE_POOLS.md) ·
[Observability](docs/OBSERVABILITY.md) ·
[Architecture review](docs/ARCHITECTURE_REVIEW.md) ·
[Benchmark template](docs/BENCHMARK_REPORT_TEMPLATE.md) ·
[Contributing](CONTRIBUTING.md) ·
[Security policy](SECURITY.md)
