#!/usr/bin/env bash
# Bootstrap the generic local workload reference cluster. It installs no
# application package; packages are rendered by the compiler and reconciled by
# the management cluster's Argo CD controller.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"
kb_require_lima_home

cluster="${WORKLOAD_CLUSTER:-workload}"
require_bins limactl kubectl python3

# Mutual exclusion only holds if both entrypoints take the same lock. Removing
# this block from one side left the other still acquiring it, which is worse
# than no lock at all: bootstrap.sh serialises against a peer that no longer
# waits for anything. The host headroom gates ride along inside the wrapper.
if [ "${INFRA_BOOTSTRAP_GUARDED:-0}" != "1" ]; then
  export INFRA_BOOTSTRAP_GUARDED=1
  infra_with_host_workload_lock "$cluster bootstrap" bash "$0" "$@"
  exit $?
fi

preflight_output="$(python3 "$infra_root/scripts/kubeadm_preflight.py" \
  --clusters "$cluster" 2>&1 || true)"
preflight_problems=()
while IFS= read -r line; do
  [ -n "$line" ] || continue
  printf '%s\n' "$line" >&2
  case "$line" in
    *"host ports already in use"*)
      occupied="${line##*: }"
      occupied="${occupied#[}"
      occupied="${occupied%]}"
      occupied="${occupied// /}"
      occupied="$(printf '%s' "$occupied" | tr ',' '\n' | sort -n | paste -sd, -)"
      expected_ports="$(cluster_ports_bound_by_running_vms "$cluster")"
      cp_vm="$(kb_cluster_vms "$cluster" | awk '{print $1; exit}')"
      if [ -n "$expected_ports" ] && [ "$occupied" = "$expected_ports" ] \
        && [ "$(kb_vm_state "$cp_vm")" = "Running" ]; then
        echo "ports are already bound by the running cluster VMs: $occupied" >&2
      else
        preflight_problems+=("$line")
      fi
      ;;
    *)
      preflight_problems+=("$line")
      ;;
  esac
done <<< "$preflight_output"
if [ "${#preflight_problems[@]}" -gt 0 ]; then
  exit 1
fi

kb_ensure_cluster "$cluster"
chmod 600 "$(kb_kubeconfig_path "$cluster")"

if cluster_exists "$infra_cluster"; then
  register_argocd_cluster "$cluster"
  restart_argocd_cluster_cache
else
  echo "management cluster absent; register '$cluster' with Argo CD after bootstrap"
fi

echo "$cluster reference cluster ready."
