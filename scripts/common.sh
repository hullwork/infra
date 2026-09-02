#!/usr/bin/env bash
# Shared environment for the product-neutral Infra core.  Product deployment
# logic belongs in product-owned Helm packages or an explicitly supplied
# external IntegrationPlugin, never in this repository.

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Sourced before the variables below: kb_state_dir and kb_kubeconfig_path are
# the single resolvers for on-host state, and everything here derives from them.
# shellcheck source=lib/kubeadm-bootstrap.sh
source "$infra_root/scripts/lib/kubeadm-bootstrap.sh"
# shellcheck source=lib/host-workload-guard.sh
source "$infra_root/scripts/lib/host-workload-guard.sh"

infra_cluster="${INFRA_CLUSTER:-infra}"
infra_dir="$(kb_state_dir)"
# Derived, not re-spelled: this file and kubeadm_profile.py each used to compose
# the path themselves and named two different directories, so bootstrap.sh
# created one and chmod-ed the other.
infra_kubeconfig="${INFRA_KUBECONFIG:-$(kb_kubeconfig_path "$infra_cluster")}"
git_server_image="${GIT_SERVER_IMAGE:-infra-git-server:2.54.0-r0}"
git_host_url="${INFRA_GIT_URL:-git://127.0.0.1:19418/gitops.git}"

require_bins() {
  local missing=0 binary
  for binary in "$@"; do
    if ! command -v "$binary" >/dev/null 2>&1; then
      echo "missing required command: $binary" >&2
      missing=1
    fi
  done
  return "$missing"
}

kube_infra() {
  kubectl --kubeconfig "$infra_kubeconfig" --context "$infra_cluster" "$@"
}

cluster_exists() {
  kb_cluster_ready "$1"
}

cluster_ports_bound_by_running_vms() {
  local cluster="${1:?cluster_ports_bound_by_running_vms: cluster is required}"
  local vm
  for vm in $(kb_cluster_vms "$cluster"); do
    [[ "$(kb_vm_state "$vm")" == "Running" ]] || continue
    awk '/^portForwards:/ { pf = 1; next }
         pf && /hostPort:/ { print $2 }
         pf && /^[^ \t]/ { pf = 0 }' "$infra_root/lima/$vm.yaml"
  done | sort -n | paste -sd, -
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

load_image_into_cluster() {
  local cluster="$1" image="$2" vm
  infra_check_host_headroom
  vm="$(kb_cluster_vms "$cluster" | awk '{print $1}')"
  docker save "$image" | limactl shell "$vm" -- sudo nerdctl --namespace k8s.io load
}

register_argocd_cluster() {
  local cluster="${1:?register_argocd_cluster: cluster is required}"
  local kubeconfig endpoint_ip ca_data cert_data key_data
  kubeconfig="$(kb_kubeconfig_path "$cluster")"
  endpoint_ip="$(kb_cluster_ip "$cluster")"
  ca_data="$(kubectl --kubeconfig "$kubeconfig" config view --raw \
    -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
  cert_data="$(kubectl --kubeconfig "$kubeconfig" config view --raw \
    -o jsonpath='{.users[0].user.client-certificate-data}')"
  key_data="$(kubectl --kubeconfig "$kubeconfig" config view --raw \
    -o jsonpath='{.users[0].user.client-key-data}')"
  if [ -z "$ca_data" ] || [ -z "$cert_data" ] || [ -z "$key_data" ]; then
    echo "failed to read client credentials from kubeconfig: $cluster" >&2
    return 1
  fi

  ARGOCD_CLUSTER="$cluster" \
  ARGOCD_ENDPOINT="$endpoint_ip" \
  ARGOCD_CA_DATA="$ca_data" \
  ARGOCD_CERT_DATA="$cert_data" \
  ARGOCD_KEY_DATA="$key_data" \
    python3 - <<'PY' | kube_infra apply -f - >/dev/null
import json
import os

cluster = os.environ["ARGOCD_CLUSTER"]
config = {
    "tlsClientConfig": {
        "caData": os.environ["ARGOCD_CA_DATA"],
        "certData": os.environ["ARGOCD_CERT_DATA"],
        "keyData": os.environ["ARGOCD_KEY_DATA"],
    }
}
secret = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {
        "name": f"cluster-{cluster}",
        "namespace": "argocd",
        "labels": {"argocd.argoproj.io/secret-type": "cluster"},
    },
    "type": "Opaque",
    "stringData": {
        "name": cluster,
        "server": f"https://{os.environ['ARGOCD_ENDPOINT']}:6443",
        "config": json.dumps(config),
    },
}
print(json.dumps(secret))
PY
  unset ca_data cert_data key_data
}

restart_argocd_cluster_cache() {
  if ! kube_infra -n argocd get statefulset argocd-application-controller \
    >/dev/null 2>&1; then
    return 0
  fi
  kube_infra -n argocd rollout restart statefulset/argocd-application-controller >/dev/null
  kube_infra -n argocd rollout status statefulset/argocd-application-controller \
    --timeout=180s >/dev/null
  echo "Argo CD cluster connection cache restarted"
}
