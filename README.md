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

> **Where this fits.** infra is one of several independently released repositories in this
> platform. [`hullwork/platform-composition`](https://github.com/hullwork/platform-composition)
> is the only place that describes all of them together: what each one is, where the
> boundaries between them are, and how to install the set on an enterprise cluster. This
> README does not repeat any of that - it is about infra alone. (Naming the other
> repositories here would violate the neutrality this file just claimed;
> `platform-composition` is data about them, which is exactly what this compiler consumes.)

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

The default example is one demo OCI Helm package. Each input is a `make`
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

Two settings decide whether a reconciliation can destroy data or pull from an
unreviewed source. Both default to the safe answer and both are widened only by
a reviewed Git change.

| Setting | Default | What widening it allows |
| --- | --- | --- |
| `ClusterProfile.spec.syncPolicy.prune` | `false` | Argo CD deletes every live object that leaves the stack |
| `bootstrap/argocd-project.yaml` `sourceRepos` | in-cluster Git daemon only | the listed repository may be reconciled into the cluster |

`prune` is off because deletion is the one reconciliation outcome no rollback
undoes: a removed `StatefulSet` takes its `PersistentVolumeClaim` with it. Every
rendered application also carries `ServerSideApply=true`, which makes this
sharper rather than softer — under server-side apply the fields Argo CD does not
own are excluded from the diff, so drift stops being reported while pruning
would keep deleting. `prune: false` is written into the rendered output
explicitly, so the effective policy is readable in the diff instead of being
inherited from an Argo CD default. Automated create and update, and self-heal,
stay on; only deletion has to be a written decision:

```yaml
spec:
  syncPolicy:
    prune: true
```

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
make preflight
make bootstrap
make bootstrap-workload
```

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

The publisher accepts only Argo `Application` or `ApplicationSet` resources and
applies no workload directly. Production should use a protected external Git
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

**Shell is thinly checked.** The repository has 13 shell files totalling about
2,200 lines. `bash -n` covers 7 of them, roughly 880 lines. The largest file,
`scripts/lib/kubeadm-bootstrap.sh` at about 890 lines, is not covered. There is
no `shellcheck` in CI.

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
