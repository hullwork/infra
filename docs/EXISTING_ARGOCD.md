# Use an existing Argo CD installation

Infra validates package records and writes Argo CD resources. It does not need a
local management cluster, Lima, or a companion repository. This walkthrough uses
a third-party chart and your existing Git/Argo CD infrastructure.

For a first installation with no additional CRDs or cluster-wide workload
resources, use [the podinfo walkthrough](../examples/hello/README.md). The
cert-manager example below is intended for operators installing a platform add-on.

## Prerequisites

- Python 3.10+ with `requirements-dev.txt` installed in a virtual environment.
- An Argo CD installation with ApplicationSet support. OCI packages additionally
  require a version supporting OCI directory sources; this example uses Helm.
  See the exact scope in [compatibility](COMPATIBILITY.md).
- A target cluster already registered with Argo CD, and an operator-approved
  AppProject permitting the chart repository, destination and required resources.
- A Git repository Argo CD can read; repository credentials stay in Argo CD.

For the default child-deletion policy, have the operator enable per-set overrides
in `argocd-cmd-params-cm`: `applicationsetcontroller.enable.policy.override: "true"`,
then restart the ApplicationSet controller. If a global controller policy overrides
this, the profile cannot enforce `applicationsSync` by itself. Resource preservation
is a separate setting; read [deletion policy and migration](CONFIGURATION.md#deletion-policy-and-the-argo-cd-project).

## Prepare and render

```bash
git clone https://github.com/hullwork/infra.git
cd infra
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
mkdir -p .state/operator
cp examples/third-party/profile.yaml .state/operator/profile.yaml
```

Edit the copied profile: replace the target cluster's `.invalid` server URL with
the registered Kubernetes API address, set `spec.project` to the approved project,
and set `spec.argocdNamespace` to Argo CD's actual installation namespace (default
`argocd`). Keep the example's target labels. The child workload namespace comes
from the Package; changing the control namespace does not change it.

The example selects cert-manager. Before installing it, review its current
[upstream installation guidance](https://cert-manager.io/docs/installation/helm/),
choose an approved version in a copied VersionLock, and confirm it is not already
owned by another installation. Never deploy this fixture over an existing release.

```bash
cp examples/third-party/versions.lock.yaml .state/operator/versions.lock.yaml
# Edit the copied lock to the approved chart version before proceeding.
make validate CATALOG=examples/third-party/catalog \
  STACK=examples/third-party/stack.yaml PROFILE=.state/operator/profile.yaml \
  LOCK=.state/operator/versions.lock.yaml
scripts/infra-python.sh scripts/infra.py render \
  --catalog examples/third-party/catalog --stack examples/third-party/stack.yaml \
  --profile .state/operator/profile.yaml --lock .state/operator/versions.lock.yaml \
  --format applicationset --output .state/operator/applications.yaml
```

The shipped default demo uses a placeholder registry and digest: it is a render
fixture, not an installable package. Review the generated namespaces, source,
version, destination and deletion controls before publication. The third-party
example has automatic workload sync disabled; keep it that way for first acceptance.

## Connect the rendered directory to Git

Copy the reviewed output to `rendered/demo/applications.yaml` in **your GitOps
repository**, and commit/push it using that repository's review process. Register
one parent Application with the following shape, replacing every placeholder:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: infra-rendered-demo
  namespace: REPLACE_ME_ARGOCD_NAMESPACE
spec:
  project: REPLACE_ME_DELIVERY_PROJECT
  source:
    repoURL: https://REPLACE_ME_GIT_HOST/REPLACE_ME_REPOSITORY.git
    targetRevision: main
    path: rendered/demo
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: REPLACE_ME_ARGOCD_NAMESPACE
  syncPolicy:
    automated:
      prune: false
      selfHeal: true
```

The delivery project must allow the Git source, the management cluster and the
control namespace, and namespaced Application/ApplicationSet resources. This
project is distinct from the workload project's permissions if you separate them.
Keep its privileges limited to the reviewed sources and resources.

Save the completed parent as `.state/operator/root.yaml`. The initial registration
is the only direct apply in this flow:

```bash
kubectl --context REPLACE_ME_MANAGEMENT_CONTEXT apply -f .state/operator/root.yaml
```

Argo CD now watches `rendered/demo` on `main`. Later reviewed Git changes and
reverts update the child definitions without rerunning a publisher. Workload
sync follows the child profile: manually sync the reviewed third-party Application
for this example. A Git revert does not undo a data migration.

## Verify reconciliation

1. Confirm the parent is Synced and its ApplicationSet is present in the configured
   control namespace. Inspect controller errors for project/repository failures.
2. Confirm the generated Application has the approved chart version and destination,
   and the ApplicationSet has the requested deletion/retention settings.
3. Sync the child through your usual Argo CD interface, then verify workload health.
4. On a disposable installation, commit a harmless supported chart value change,
   observe parent/child updates, then revert it and verify reconciliation again.
5. Rehearse retirement separately: the parent's `prune: false` retains removed child
   definitions and can leave the parent OutOfSync until explicit retirement.
   ApplicationSet `create-update` also retains removed generated
   Applications when honored. Remove retained control resources only after reviewing
   their finalizers, workload retention and data backups.

A successful render or Synced parent alone does not prove application readiness,
resource retention or recovery. Existing child finalizers must be reviewed when
migrating from older compiler output. Direct administrator deletion is outside
these reconciliation settings.

## Optional local publisher

For the experimental local lab, `make publish-rendered RENDERED_NAME=demo
RENDERED_FILE=/absolute/path/applications.yaml` commits the same watched directory
and registers `infra-rendered-demo`. `ARGOCD_NAMESPACE` defaults to `argocd` and
must match the rendered children. `INFRA_GIT_URL` is the host-side push endpoint;
`INFRA_GIT_CLUSTER_URL` is Argo CD's read endpoint. `INFRA_ROOT_PROJECT` defaults
to `default`. The local bootstrap uses `argocd`; changing publisher settings does
not relocate that bootstrap installation. See [local isolation](LOCAL_REFERENCE.md).
