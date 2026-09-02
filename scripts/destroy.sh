#!/usr/bin/env bash
# Stop local reference clusters while retaining their disks and ignored state.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"

require_bins limactl

clusters=("${INFRA_CLUSTER:-infra}" "${WORKLOAD_CLUSTER:-workload}")
for cluster in "${clusters[@]}"; do
  for vm in $(kb_cluster_vms "$cluster"); do
    if kb_vm_exists "$vm"; then
      kb_vm_down "$vm"
      echo "stopped VM (disk retained): $vm"
    else
      echo "VM already absent: $vm"
    fi
  done
done

echo "Local state retained at: $infra_dir"
