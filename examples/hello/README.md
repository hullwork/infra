# First deployment: podinfo

Deploy one small HTTP application into `infra-hello` using an existing Argo CD
installation. No extra controller, CRD, ingress, storage, registry account or
companion source checkout is needed. The upstream chart is pinned to a Git
commit; its container image uses the upstream version tag. This is an evaluation
example, not a claim of image digest verification.

## Prerequisites

- Python 3.10+ and the dependencies from the repository root README.
- An existing Argo CD installation; see [compatibility](../../docs/COMPATIBILITY.md).
- Access to an unused `infra-hello` namespace in the same cluster as Argo CD.
- An operator who can create the namespace and the narrow AppProject below.
- A GitOps repository readable by Argo CD, and permission to register a parent
  Application watching its rendered directory.

Argo CD deploys only a Deployment and Service from this example. The upstream
chart also renders Helm test Pods, which Argo CD ignores as unsupported
[test hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/#helm-hooks).
Setting up Argo CD,
the AppProject and namespace is an operator task. Application sync is manual by
default. Never reuse a namespace already owned by another deployment.

## Render without a cluster

From the repository root:

```bash
mkdir -p .state/hello
cp examples/hello/profile.yaml .state/hello/profile.yaml
cp examples/hello/project.yaml .state/hello/project.yaml
make validate CATALOG=examples/hello/catalog STACK=examples/hello/stack.yaml \
  PROFILE=.state/hello/profile.yaml LOCK=examples/hello/versions.lock.yaml
scripts/infra-python.sh scripts/infra.py render \
  --catalog examples/hello/catalog --stack examples/hello/stack.yaml \
  --profile .state/hello/profile.yaml --lock examples/hello/versions.lock.yaml \
  --format applicationset --output .state/hello/applications.yaml
```

Expected: validation succeeds and the output contains one ApplicationSet named
`hello-podinfo`. Its generated Application is `local-hello-podinfo`, targets
`infra-hello`, and uses the commit in `versions.lock.yaml`.

If Argo CD is in a custom namespace, change `spec.argocdNamespace` in the copied
profile and `metadata.namespace` in `.state/hello/project.yaml`. For a different
target cluster, also change the server in the profile and AppProject destination.
Set `HELLO_WORKLOAD_CONTEXT` below to that cluster's context. Re-render after edits.

## Register with GitOps

Set `HELLO_CONTEXT` to the intended management cluster context, then have the
operator create the namespace and apply the reviewed project:

```bash
export HELLO_CONTEXT=REPLACE_ME
export HELLO_WORKLOAD_CONTEXT="$HELLO_CONTEXT" # change for a separate workload cluster
kubectl --context "$HELLO_WORKLOAD_CONTEXT" create namespace infra-hello
kubectl --context "$HELLO_CONTEXT" apply -f .state/hello/project.yaml
```

Follow [the existing-Argo-CD guide](../../docs/EXISTING_ARGOCD.md#connect-the-rendered-directory-to-git)
to commit `.state/hello/applications.yaml` as `rendered/hello/applications.yaml`
in your GitOps repository. Use parent name `infra-rendered-hello` and path
`rendered/hello`. The parent delivery AppProject must permit that Git repository
and ApplicationSet/Application resources in the Argo CD namespace; it is separate
from the workload AppProject shipped here.

Enable per-ApplicationSet policy overrides as described in that guide. Verify
the parent is Synced and `local-hello-podinfo` exists. Review and sync that child
Application in Argo CD. The parent syncing does not automatically sync this child.

## Check the actual application

Use the workload context set above:

```bash
kubectl --context "$HELLO_WORKLOAD_CONTEXT" -n infra-hello \
  rollout status deployment/podinfo --timeout=180s
kubectl --context "$HELLO_WORKLOAD_CONTEXT" -n infra-hello \
  port-forward service/podinfo 19898:9898 --address 127.0.0.1
```

Open `http://127.0.0.1:19898` in a browser. In another terminal, request JSON:

```bash
curl --fail http://127.0.0.1:19898/api/info
```

Expected: the page and JSON show `Hello from Infra`; the JSON reports version
`6.15.0`. Argo CD should report the child as Synced and Healthy.
Port-forwarding attaches to a Pod; rerun the port-forward command if a rollout
replaces that Pod and closes the connection.

## Update, revert and retire

1. In a copy of the catalog, change `spec.values.ui.message` to `Hello again`.
   Render using that catalog, review and commit the updated output to GitOps.
2. Wait for the parent to sync, manually sync the child, then verify the browser
   and JSON show `Hello again`.
3. Revert the GitOps commit. After parent and child sync, verify `Hello from Infra`
   returns. This tests configuration rollback, not data recovery.
4. In this disposable example, remove the child definition from Git. The parent
   has pruning disabled, so the ApplicationSet and workload remain. The parent
   can report OutOfSync with a resource requiring pruning; this is the expected
   retained-resource state, not a failed rollback. Retire the
   retained ApplicationSet explicitly after checking child finalizers; the
   preservation policy should retain the workload even if its Application is
   garbage-collected. Verify the Deployment and Service still exist.
5. To remove the demo fully, stop port-forwarding and delete only its dedicated
   namespace and the two demo AppProjects/parent resources you created. Review
   names and contents before deletion; a parent project may be shared.

Source tests do not prove these runtime steps. Recorded acceptance and its limits
are tracked in [release readiness](../../docs/RELEASE_READINESS.md).
