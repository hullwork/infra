#!/usr/bin/env bash
# Bootstrap the product-neutral local GitOps management plane.  Workload
# packages are rendered separately with scripts/infra.py and reconciled by
# Argo CD; this entrypoint deliberately installs no application package.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"

require_bins curl docker git kubectl limactl python3 shasum
infra_require_clean_main_source "$infra_root"

if [ "${INFRA_BOOTSTRAP_GUARDED:-0}" != "1" ]; then
  export INFRA_BOOTSTRAP_GUARDED=1
  infra_with_host_workload_lock "infra management bootstrap" bash "$0" "$@"
  exit $?
fi

argocd_version="${ARGOCD_VERSION:-v3.5.0}"
argocd_manifest_sha256="${ARGOCD_CORE_MANIFEST_SHA256:-166bffca5000482f8ae36d73b255610c9bb4c7d8da38fcf62003c2fdd8ab0747}"
argocd_manifest_url="https://raw.githubusercontent.com/argoproj/argo-cd/${argocd_version}/manifests/core-install.yaml"
argocd_manifest="$infra_dir/argocd-core-${argocd_version}.yaml"

mkdir -p "$infra_dir"
kb_ensure_cluster "$infra_cluster"
chmod 600 "$infra_kubeconfig"

docker build -t "$git_server_image" \
  -f "$infra_root/bootstrap/git-server.Dockerfile" \
  "$infra_root/bootstrap"
load_image_into_cluster "$infra_cluster" "$git_server_image"

kube_infra apply -f "$infra_root/bootstrap/git-server.yaml"
kube_infra -n gitops-system rollout status deployment/git-server --timeout=180s

if [ ! -f "$argocd_manifest" ] ||
  [ "$(sha256_file "$argocd_manifest")" != "$argocd_manifest_sha256" ]; then
  manifest_tmp="${argocd_manifest}.tmp"
  curl -fsSLo "$manifest_tmp" "$argocd_manifest_url"
  downloaded_sha256="$(sha256_file "$manifest_tmp")"
  if [ "$downloaded_sha256" != "$argocd_manifest_sha256" ]; then
    rm -f "$manifest_tmp"
    echo "Argo CD manifest checksum mismatch: expected $argocd_manifest_sha256, got $downloaded_sha256" >&2
    exit 1
  fi
  mv "$manifest_tmp" "$argocd_manifest"
fi

kube_infra create namespace argocd --dry-run=client -o yaml |
  kube_infra apply -f -
kube_infra -n argocd apply --server-side --force-conflicts -f "$argocd_manifest"
kube_infra -n argocd apply -f "$infra_root/bootstrap/argocd-project.yaml"
kube_infra -n argocd rollout status deployment/argocd-redis --timeout=240s
kube_infra -n argocd rollout status deployment/argocd-repo-server --timeout=240s
kube_infra -n argocd rollout status deployment/argocd-applicationset-controller --timeout=240s
kube_infra -n argocd rollout status statefulset/argocd-application-controller --timeout=240s

echo "Infra management plane ready."
echo "Render a package stack with: make render CATALOG=... STACK=... PROFILE=... LOCK=..."
echo "Kubeconfig: $infra_kubeconfig"
