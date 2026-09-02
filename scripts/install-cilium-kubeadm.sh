#!/usr/bin/env bash
# Install the checksum-pinned Cilium chart in a infra managed kubeadm cluster.
set -euo pipefail

cluster="${1:?usage: install-cilium-kubeadm.sh <cluster>}"
cilium_version="${CILIUM_VERSION:-1.19.6}"
cilium_chart_sha256="${CILIUM_CHART_SHA256:-21c43cf53841f9ab0375047d95aa4c64051ea52bbd2c679416e6408f5f1c9179}"

: "${KUBECONFIG:?set KUBECONFIG to the target kubeadm cluster configuration}"
export KUBECONFIG

task_cache_dir="${INFRA_CACHE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.state/cache}/helm"
mkdir -p "$task_cache_dir"

for command in helm shasum; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "missing required command: $command" >&2
    exit 1
  fi
done

if [ ! -s "${KUBECONFIG%%:*}" ]; then
  echo "kubeconfig not found or empty: $KUBECONFIG" >&2
  exit 1
fi

chart_file="$task_cache_dir/cilium-${cilium_version}.tgz"
if [ ! -s "$chart_file" ] \
  || [ "$(shasum -a 256 "$chart_file" | awk '{print $1}')" != "$cilium_chart_sha256" ]; then
  rm -f -- "$chart_file"
  helm pull oci://quay.io/cilium/charts/cilium \
    --version "$cilium_version" \
    --destination "$task_cache_dir"
fi
actual_chart_sha256="$(shasum -a 256 "$chart_file" | awk '{print $1}')"
if [ "$actual_chart_sha256" != "$cilium_chart_sha256" ]; then
  echo "Cilium chart checksum mismatch: expected $cilium_chart_sha256, got $actual_chart_sha256" >&2
  exit 1
fi

helm --kube-context "$cluster" upgrade --install cilium "$chart_file" \
  --namespace kube-system \
  --set routingMode=tunnel --set tunnelProtocol=vxlan \
  --set ipam.mode=kubernetes \
  --set-string kubeProxyReplacement=false \
  --set operator.replicas=1 \
  --set-string 'operator.nodeSelector.infra\.convee\.io/node-role=system' \
  --set-string 'operator.tolerations[0].key=node-role.kubernetes.io/control-plane' \
  --set-string 'operator.tolerations[0].operator=Exists' \
  --set-string 'operator.tolerations[0].effect=NoSchedule' \
  --set-string 'operator.tolerations[1].key=node.kubernetes.io/not-ready' \
  --set-string 'operator.tolerations[1].operator=Exists' \
  --set-string 'operator.tolerations[1].effect=NoSchedule' \
  --set-string 'operator.tolerations[2].key=node.kubernetes.io/unreachable' \
  --set-string 'operator.tolerations[2].operator=Exists' \
  --set-string 'operator.tolerations[2].effect=NoSchedule' \
  --wait --timeout 10m

echo "Cilium $cilium_version (tunnel/vxlan) installed on $cluster; chart sha256: $actual_chart_sha256"
