#!/usr/bin/env bash

# Shared guard for host-heavy local operations (Docker builds and image imports).
# All local cluster entrypoints use the same lock so two independent releases
# cannot starve every Lima control plane at once.  That sentence stopped being
# true once for a whole release: one entrypoint dropped its wrapper and the
# other kept waiting on a peer that no longer took the lock.  The set of
# entrypoints is now enforced by tests/test_host_workload_lock.py, so the claim
# and the code fail together instead of drifting apart.

infra_require_clean_main_source() {
  local repo_root="${1:?repository root is required}"
  local branch head origin_head dirty toplevel

  # Three states, not two.  A released tarball is not a Git checkout, so every
  # `git` probe below fails and the guard used to report "source branch must be
  # main (current: detached HEAD)" -- blocking `make bootstrap` for everyone who
  # installed from a release asset.  Compare against the real toplevel so an
  # archive unpacked *inside* some unrelated checkout is not mistaken for one.
  toplevel="$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -z "$toplevel" ] ||
    [ "$(cd "$toplevel" 2>/dev/null && pwd -P)" != "$(cd "$repo_root" 2>/dev/null && pwd -P)" ]; then
    if [ ! -f "$repo_root/VERSION" ]; then
      printf 'release blocked: %s is neither a Git checkout nor an unpacked release archive (no VERSION file)\n' \
        "$repo_root" >&2
      return 1
    fi
    printf 'release source is an unpacked archive, not a Git checkout: branch/clean checks skipped for %s @ %s\n' \
      "$repo_root" "$(tr -d '\r\n' <"$repo_root/VERSION")" >&2
    return 0
  fi

  branch="$(git -C "$repo_root" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [ "$branch" != "main" ]; then
    printf 'release blocked: source branch must be main (current: %s)\n' \
      "${branch:-detached HEAD}" >&2
    return 1
  fi

  head="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
  origin_head="$(git -C "$repo_root" rev-parse refs/remotes/origin/main 2>/dev/null || true)"
  if [ -z "$head" ] || [ -z "$origin_head" ] || [ "$head" != "$origin_head" ]; then
    printf 'release blocked: main must exactly match origin/main (HEAD=%s origin/main=%s)\n' \
      "${head:-missing}" "${origin_head:-missing}" >&2
    return 1
  fi

  dirty="$(git -C "$repo_root" status --porcelain=v1 --untracked-files=normal \
    --ignore-submodules=none)"
  if [ -n "$dirty" ]; then
    printf 'release blocked: main worktree must be clean; commit and push first\n' >&2
    return 1
  fi

  printf 'release source verified: main @ %s\n' "$head"
}

infra_host_cpu_count() {
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu 2>/dev/null && return 0
  fi
  getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1\n'
}

infra_host_load_one() {
  if [ -r /proc/loadavg ]; then
    awk '{print $1}' /proc/loadavg
    return
  fi
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}'
    return
  fi
  printf '0\n'
}

infra_check_host_headroom() {
  if [ "${INFRA_ALLOW_HOST_PRESSURE:-0}" = "1" ]; then
    printf 'host pressure gate explicitly bypassed (INFRA_ALLOW_HOST_PRESSURE=1)\n' >&2
    return 0
  fi

  local cpu_count load_one max_load_per_cpu disk_path disk_free_kb min_disk_kb
  local memory_free_percent min_memory_free_percent failed=0
  cpu_count="$(infra_host_cpu_count)"
  load_one="$(infra_host_load_one)"
  max_load_per_cpu="${INFRA_MAX_LOAD_PER_CPU:-2}"
 # load1 instead of load: `load` is the built-in name retained by gawk, -v load=... will cause gawk to fatally exit,
 # So the gate fails silently on all Linux hosts. When awk itself makes an error, it will be released according to "not over limit".
  if awk -v load1="$load_one" -v cpus="$cpu_count" -v ratio="$max_load_per_cpu" \
      'BEGIN { exit !(load1 > cpus * ratio) }'; then
    printf 'host load too high for image build/import: load1=%s, cpu=%s, limit=%sx cpu\n' \
      "$load_one" "$cpu_count" "$max_load_per_cpu" >&2
    failed=1
  fi

  disk_path="${INFRA_HOST_DISK_PATH:-$PWD}"
  min_disk_kb="${INFRA_MIN_HOST_DISK_KB:-31457280}" # 30 GiB
  disk_free_kb="$(df -Pk "$disk_path" 2>/dev/null | awk 'NR == 2 {print $4}')"
  if [ -n "$disk_free_kb" ] && [ "$disk_free_kb" -lt "$min_disk_kb" ]; then
    printf 'host disk headroom too low for image build/import: %s GiB free, require >= %s GiB\n' \
      "$((disk_free_kb / 1024 / 1024))" "$((min_disk_kb / 1024 / 1024))" >&2
    failed=1
  fi

  min_memory_free_percent="${INFRA_MIN_MEMORY_FREE_PERCENT:-10}"
  if command -v memory_pressure >/dev/null 2>&1; then
    memory_free_percent="$(memory_pressure -Q 2>/dev/null \
      | sed -n 's/^System-wide memory free percentage: \([0-9][0-9]*\)%$/\1/p')"
    if [ -n "$memory_free_percent" ] \
        && [ "$memory_free_percent" -lt "$min_memory_free_percent" ]; then
      printf 'host memory pressure too high for image build/import: %s%% free, require >= %s%%\n' \
        "$memory_free_percent" "$min_memory_free_percent" >&2
      failed=1
    fi
  fi

  if [ "$failed" -ne 0 ]; then
    printf '%s\n' \
      'heavy host operation blocked to protect Lima control planes; stop optional VMs/tasks or reclaim build cache, then retry' >&2
    return 1
  fi
}

infra_host_lock_read() {
  sed -n '1p' "$1" 2>/dev/null || true
}

#Empty pid is also considered dead: the old version of double mkdir will leave an empty lock directory without an owner file, and it will be permanently deadlocked if it is not taken over.
infra_host_lock_owner_is_dead() {
  case "$1" in
    ''|*[!0-9]*) return 0 ;;
    *) kill -0 "$1" 2>/dev/null && return 1 || return 0 ;;
  esac
}

infra_with_host_workload_lock() (
  set -e
  local label="${1:-unnamed workload}"
  shift
  # EXIT runs after function locals unwind on Bash 3.2 error exits. Keep the
  # cleanup paths in this function's isolated subshell, not in local scope.
  local owner_pid lock_owner salvage held
  lock_dir="${INFRA_HOST_WORKLOAD_LOCK_DIR:-${TMPDIR:-/tmp}/infra-host-workload.lock}"
  owner_file="$lock_dir/pid"
  label_file="$lock_dir/label"
  lock_owner="${BASHPID:-$$}"
  held=0

 # If mkdir succeeds, the lock is held - it is a locking action in itself, and you cannot mkdir a second time for the purpose of "reconfirmation"
 # (This is the case in the old version, so it will inevitably fail the second time, and the lock on the clean host will never be obtained).
  if mkdir "$lock_dir" 2>/dev/null; then
    held=1
  else
    owner_pid="$(infra_host_lock_read "$owner_file")"
    if [ -z "$owner_pid" ]; then
 # The holder may be in the window between mkdir and writing pid, give it a chance to make a decision again.
      sleep 0.1
      owner_pid="$(infra_host_lock_read "$owner_file")"
    fi
    if infra_host_lock_owner_is_dead "$owner_pid"; then
 # mv the entire residual directory to a unique name: mv atomic. Only one process gets the cleanup right during concurrency.
 # There will be no double holding of "the two processes each rmdir and then each mkdir succeeds".
      salvage="$lock_dir.stale.$lock_owner"
      if mv "$lock_dir" "$salvage" 2>/dev/null; then
        if [ "$(infra_host_lock_read "$salvage/pid")" = "$owner_pid" ]; then
          rm -rf "$salvage"
        elif [ ! -e "$lock_dir" ] && mv "$salvage" "$lock_dir" 2>/dev/null; then
          : # What we moved aside was a live lock another holder had just created; it is
            # back in place, and the "occupied" rejection below applies.
        else
          rm -rf "$salvage"
        fi
      fi
      if mkdir "$lock_dir" 2>/dev/null; then
        held=1
      fi
    fi
  fi

  if [ "$held" -ne 1 ]; then
    printf 'another infra host-heavy operation is active: pid=%s label=%s\n' \
      "$(infra_host_lock_read "$owner_file")" \
      "$(infra_host_lock_read "$label_file")" >&2
    return 1
  fi

  printf '%s\n' "$lock_owner" >"$owner_file"
  printf '%s\n' "$label" >"$label_file"
  infra_release_host_workload_lock() {
    rm -f "$owner_file" "$label_file"
    rmdir "$lock_dir" 2>/dev/null || true
  }
  trap infra_release_host_workload_lock EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  infra_check_host_headroom
  "$@"
)
