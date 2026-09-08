#!/usr/bin/env bash
# Publish compiler output to the local GitOps repository, then register that
# parent Application that continuously reconciles the published directory. This is generic render delivery; it
# never builds an application or interprets application-specific values.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"

name="${1:?usage: publish-rendered.sh NAME RENDERED_YAML}"
manifest="${2:?usage: publish-rendered.sh NAME RENDERED_YAML}"

[[ "$name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || {
  echo "invalid rendered stack name: $name" >&2
  exit 2
}
[ -s "$manifest" ] || {
  echo "missing rendered manifest: $manifest" >&2
  exit 2
}

infra_require_clean_main_source "$infra_root"
require_bins git kubectl
kube_infra -n gitops-system rollout status deployment/git-server --timeout=180s

deadline=$((SECONDS + 60))
until git ls-remote "$git_host_url" >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || {
    echo "local GitOps repository is unreachable: $git_host_url" >&2
    exit 1
  }
  sleep 2
done

"$infra_root/scripts/infra-python.sh" - "$manifest" "${ARGOCD_NAMESPACE:-argocd}" <<'PY'
import sys
from pathlib import Path

import yaml

allowed = {"Application", "ApplicationSet"}
documents = list(yaml.safe_load_all(Path(sys.argv[1]).read_text(encoding="utf-8")))
if not documents or any(not isinstance(document, dict) or document.get("kind") not in allowed
                        or document.get("apiVersion") != "argoproj.io/v1alpha1"
                        for document in documents):
    raise SystemExit("rendered input must contain only Argo Application/ApplicationSet resources")
if any(document.get("metadata", {}).get("namespace") != sys.argv[2] for document in documents):
    raise SystemExit("rendered namespace must match ARGOCD_NAMESPACE")
PY

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/infra-rendered.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
checkout="$work_dir/repo"
path="rendered/$name"
git clone --quiet "$git_host_url" "$checkout"
mkdir -p "$checkout/$path"
cp "$manifest" "$checkout/$path/all.yaml"
git -C "$checkout" config user.name "Infra GitOps"
git -C "$checkout" config user.email "infra-gitops@localhost"
git -C "$checkout" add "$path"
if ! git -C "$checkout" diff --cached --quiet; then
  git -C "$checkout" commit --quiet -m "chore(gitops): publish rendered $name"
  git -C "$checkout" push --quiet origin main
else
  echo "GitOps repository already contains rendered/$name"
fi

# Only the root registration is imperative. Its source continuously watches
# rendered/<name> in Git, including later commits and reverts.
"$infra_root/scripts/infra-python.sh" - "$name" "${ARGOCD_NAMESPACE:-argocd}" \
  "${INFRA_GIT_CLUSTER_URL:-git://git-server.gitops-system.svc.cluster.local/gitops.git}" \
  "${INFRA_ROOT_PROJECT:-default}" <<'PY' | kube_infra apply -f -
import sys
import yaml

name, namespace, repository, project = sys.argv[1:]
root = {
    "apiVersion": "argoproj.io/v1alpha1",
    "kind": "Application",
    "metadata": {"name": "infra-rendered-" + name, "namespace": namespace},
    "spec": {
        "project": project,
        "source": {"repoURL": repository, "targetRevision": "main",
                   "path": "rendered/" + name, "directory": {"recurse": True}},
        "destination": {"server": "https://kubernetes.default.svc", "namespace": namespace},
        "syncPolicy": {"automated": {"prune": False, "selfHeal": True}},
    },
}
print(yaml.safe_dump(root, sort_keys=False))
PY
echo "published rendered/$name; parent infra-rendered-$name now reconciles Git"
echo "Removed child definitions are retained (root prune=false); retire them explicitly after reviewing data retention."
