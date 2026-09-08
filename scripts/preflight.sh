#!/usr/bin/env bash
# Fail fast before the local management cluster is created or upgraded.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"
kb_require_lima_home

problems=()
warnings=()
note_problem() { problems+=("$1"); }
note_warning() { warnings+=("$1"); }

require_bins curl docker git kubectl limactl python3 shasum

cpu_count="$(infra_host_cpu_count)"
memory_free_percent="$(memory_pressure -Q 2>/dev/null | sed -n 's/^System-wide memory free percentage: \([0-9][0-9]*\)%$/\1/p' || true)"
disk_free_kb="$(df -Pk "$infra_root" | awk 'NR==2 {print $4}')"

[ "$cpu_count" -ge 4 ] ||
  note_warning "CPU count is below 4 (found $cpu_count)"
[ -z "$memory_free_percent" ] || [ "$memory_free_percent" -ge 10 ] ||
  note_warning "memory free percentage is below 10% (found ${memory_free_percent}%)"
[ -z "$disk_free_kb" ] || [ "$disk_free_kb" -ge 31457280 ] ||
  note_warning "free disk is below 30 GiB (found $((disk_free_kb / 1024 / 1024)) GiB)"

preflight_output="$(python3 "$infra_root/scripts/kubeadm_preflight.py" --clusters "$infra_cluster" 2>&1 || true)"
while IFS= read -r line; do
  [ -n "$line" ] || continue
  printf '%s\n' "$line" >&2
  case "$line" in
    *"disk space is low"*)
      note_warning "$line"
      ;;
    *"host ports already in use"*)
      occupied="${line##*: }"
      occupied="${occupied#[}"
      occupied="${occupied%]}"
      occupied="${occupied// /}"
      occupied="$(printf '%s' "$occupied" | tr ',' '\n' | sort -n | paste -sd, -)"
      expected_ports="$(cluster_ports_bound_by_running_vms "$infra_cluster")"
      if [ -n "$expected_ports" ] && [ "$occupied" = "$expected_ports" ] \
        && [ "$(kb_vm_state "$(kb_cluster_vms "$infra_cluster" | awk '{print $1}')")" = "Running" ]; then
        note_warning "ports are already bound by the running management VM: $occupied"
      else
        note_problem "$line"
      fi
      ;;
    *)
      note_problem "$line"
      ;;
  esac
done <<< "$preflight_output"

if [ "${#warnings[@]}" -gt 0 ]; then
  echo "Warnings (non-blocking):"
  for warning in ${warnings[@]+"${warnings[@]}"}; do echo "  - $warning"; done
fi
if [ "${#problems[@]}" -gt 0 ]; then
  echo "Preflight failed:" >&2
  for problem in ${problems[@]+"${problems[@]}"}; do echo "  - $problem" >&2; done
  exit 1
fi

echo "Preflight passed."
