#!/usr/bin/env bash
# Built-in local NodePool provider. Scale-down stops VMs and retains their disks.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
infra_root="$(cd "$script_dir/../.." && pwd)"
export KB_INFRA_ROOT="${KB_INFRA_ROOT:-$infra_root}"
# shellcheck source=../lib/kubeadm-bootstrap.sh
source "$infra_root/scripts/lib/kubeadm-bootstrap.sh"

action="${1:-}"
shift || true
pool_file=""
desired=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pool) pool_file="${2:?missing --pool value}"; shift 2 ;;
    --desired) desired="${2:?missing --desired value}"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[ "$action" = reconcile ] && [ -s "$pool_file" ] && [[ "$desired" =~ ^[0-9]+$ ]] || {
  echo 'usage: lima-kubeadm-nodepool.sh reconcile --pool FILE --desired N' >&2
  exit 2
}

read_pool() {
  python3 - "$pool_file" "$1" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
value = doc
for key in sys.argv[2].split("."):
    value = value[key]
if isinstance(value, list):
    print(" ".join(value))
elif isinstance(value, bool):
    print(str(value).lower())
else:
    print(value)
PY
}

cluster="$(read_pool spec.cluster)"
read_workers() {
  python3 - "$pool_file" "$1" <<'PY'
import sys, yaml
workers = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["spec"]["provider"]["parameters"]["workers"]
print(" ".join(worker[sys.argv[2]] for worker in workers))
PY
}
worker_vms="$(read_workers vm)"
worker_nodes="$(read_workers node)"
node_labels="$(python3 - "$pool_file" <<'PY'
import sys, yaml
labels = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["spec"]["provider"]["parameters"]["labels"]
print(" ".join(f"{key}={value}" for key, value in labels.items()))
PY
)"
node_taints="$(python3 - "$pool_file" <<'PY'
import sys, yaml
taints = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["spec"]["provider"]["parameters"]["taints"]
print(" ".join(taints))
PY
)"
probe_tolerations="$(python3 - "$pool_file" <<'PY'
import json, sys, yaml
taints = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["spec"]["provider"]["parameters"]["taints"]
items = []
for taint in taints:
    assignment, effect = taint.rsplit(":", 1)
    key, value = assignment.split("=", 1)
    items.append({"key": key, "operator": "Equal", "value": value, "effect": effect})
print(json.dumps(items, separators=(",", ":")))
PY
)"
probe_node_selector="$(python3 - "$pool_file" <<'PY'
import json, sys, yaml
labels = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["spec"]["provider"]["parameters"]["labels"]
print(json.dumps(labels, separators=(",", ":")))
PY
)"
drain_timeout="$(read_pool spec.scaleDown.drainTimeoutSeconds)"
kubeconfig="$(read_pool spec.provider.parameters.kubeconfig)"
case "$kubeconfig" in /*) ;; *) kubeconfig="$infra_root/$kubeconfig" ;; esac
kb_kubeconfig_path() {
  [ "$1" = "$cluster" ] || {
    echo "provider can resolve only its declared cluster: $cluster" >&2
    return 2
  }
  printf '%s\n' "$kubeconfig"
}
event_log="$(read_pool spec.observability.eventLog)"
case "$event_log" in /*) ;; *) event_log="$infra_root/$event_log" ;; esac
mkdir -p "$(dirname "$event_log")"

emit() {
  local phase="$1" status="$2" node="${3:-}" started_ms="${4:-}"
  python3 - "$event_log" "$cluster" "$phase" "$status" "$node" "$started_ms" <<'PY'
import json, pathlib, sys, time
now = int(time.time() * 1000)
item = {"schemaVersion": 1, "timestampUnixMs": now, "cluster": sys.argv[2],
        "phase": sys.argv[3], "status": sys.argv[4], "provider": "local-lima-kubeadm"}
if sys.argv[5]: item["node"] = sys.argv[5]
if sys.argv[6]: item["durationMs"] = now - int(sys.argv[6])
with pathlib.Path(sys.argv[1]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
PY
}

read -r -a worker_vm_array <<<"$worker_vms"
read -r -a worker_node_array <<<"$worker_nodes"
[ "$desired" -le "${#worker_vm_array[@]}" ] || {
  echo "desired $desired exceeds declared workers ${#worker_vm_array[@]}" >&2
  exit 2
}
refresh_kubeconfig() {
  local cp api_port cp_ip
  cp="$(kb_cluster_vms "$cluster" | awk '{print $1}')"
  [ -n "$cp" ] && kb_vm_exists "$cp" || {
    echo "cluster $cluster has no provisioned control-plane VM" >&2
    return 1
  }
  # Always pull admin.conf again. A recreated kubeadm control plane can keep the
  # same Lima name and host port while rotating its CA; existence/readability of
  # the host kubeconfig therefore proves nothing about its identity.
  kb_vm_up "$cp"
  api_port="$(kb_api_port "$cluster")"
  cp_ip="$(kb_cluster_ip "$cluster")"
  kb_fetch_kubeconfig "$cluster" "$cp" "$kubeconfig" "$api_port" "$cp_ip"
  kb_wait_api "$cluster" "${NODEPOOL_API_TIMEOUT:-360}"
}

preflight_started_ms="$(($(date +%s) * 1000))"
emit preflight started
refresh_kubeconfig
emit preflight succeeded "" "$preflight_started_ms"
kubectl --kubeconfig "$kubeconfig" --context "$cluster" label node \
  -l node-role.kubernetes.io/control-plane infra.convee.io/node-role=system --overwrite >/dev/null

assert_runtime_boundary() {
  local node="$1" audit_file
  audit_file="$(mktemp "${TMPDIR:-/tmp}/infra-nodepool-boundary.XXXXXX")"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pods -A \
    --field-selector "spec.nodeName=$node" -o json >"$audit_file"
  if ! python3 - "$audit_file" "$pool_file" <<'PY'
import json, sys, yaml
pods = json.load(open(sys.argv[1], encoding="utf-8")).get("items", [])
parameters = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))["spec"]["provider"]["parameters"]
labels = parameters["labels"]
taints = []
for item in parameters["taints"]:
    assignment, effect = item.rsplit(":", 1)
    key, value = assignment.split("=", 1)
    taints.append((key, value, effect))
violations = []
for pod in pods:
    if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
        continue
    if any(owner.get("kind") == "DaemonSet" for owner in pod.get("metadata", {}).get("ownerReferences", [])):
        continue
    spec = pod.get("spec", {})
    selector = spec.get("nodeSelector", {})
    tolerations = spec.get("tolerations", [])
    selected = all(selector.get(key) == value for key, value in labels.items())
    tolerated = all(any(
        tolerance.get("key") == key
        and tolerance.get("operator", "Equal") == "Equal"
        and tolerance.get("value") == value
        and tolerance.get("effect") == effect
        for tolerance in tolerations
    ) for key, value, effect in taints)
    if not selected or not tolerated:
        metadata = pod.get("metadata", {})
        violations.append(f'{metadata.get("namespace")}/{metadata.get("name")}')
if violations:
    print("runtime-only node contains workload(s) without the pool selector and exact toleration: " + "; ".join(violations), file=sys.stderr)
    raise SystemExit(1)
PY
  then
    rm -f -- "$audit_file"
    return 1
  fi
  rm -f -- "$audit_file"
}

assert_idle_node() {
  local node="$1" pods
  pods="$(kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pods -A \
    --field-selector "spec.nodeName=$node" -o json | python3 -c '
import json, sys
active = []
for pod in json.load(sys.stdin).get("items", []):
    if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
        continue
    if any(owner.get("kind") == "DaemonSet" for owner in pod.get("metadata", {}).get("ownerReferences", [])):
        continue
    meta = pod.get("metadata", {})
    active.append("{}/{}".format(meta.get("namespace"), meta.get("name")))
print(";".join(active))')"
  [ -z "$pods" ] || {
    echo "scale-down is allowed only for an idle runtime node; active workload(s): $pods" >&2
    return 1
  }
}

pod_has_persistent_or_ephemeral_data() {
  local node="$1" audit_dir
  audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/infra-nodepool-audit.XXXXXX")"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pods -A \
    --field-selector "spec.nodeName=$node" -o json >"$audit_dir/pods.json"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pvc -A -o json >"$audit_dir/pvcs.json"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pv -o json >"$audit_dir/pvs.json"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" get storageclass -o json >"$audit_dir/classes.json"
  if ! python3 - "$audit_dir" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
load = lambda name: json.loads((root / name).read_text()).get("items", [])
pvcs = {(x["metadata"]["namespace"], x["metadata"]["name"]): x for x in load("pvcs.json")}
pvs = {x["metadata"]["name"]: x for x in load("pvs.json")}
classes = {x["metadata"]["name"]: x for x in load("classes.json")}
unsafe = []
for pod in load("pods.json"):
    if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
        continue
    owners = pod.get("metadata", {}).get("ownerReferences", [])
    if any(owner.get("kind") == "DaemonSet" for owner in owners):
        continue
    namespace = pod["metadata"]["namespace"]
    name = pod["metadata"]["name"]
    for volume in pod.get("spec", {}).get("volumes", []):
        if "emptyDir" in volume:
            unsafe.append(f"{namespace}/{name}:emptyDir")
        claim = volume.get("persistentVolumeClaim", {}).get("claimName")
        if not claim:
            continue
        pvc = pvcs.get((namespace, claim), {})
        pv = pvs.get(pvc.get("spec", {}).get("volumeName", ""), {})
        storage_class = classes.get(pv.get("spec", {}).get("storageClassName", ""), {})
        provisioner = storage_class.get("provisioner", "")
        if not pv or "local" in pv.get("spec", {}) or "hostPath" in pv.get("spec", {}) or "local" in provisioner:
            unsafe.append(f"{namespace}/{name}:node-local-pvc/{claim}")
if unsafe:
    print("scale-down blocked by node-local or ephemeral data: " + "; ".join(unsafe), file=sys.stderr)
    raise SystemExit(1)
PY
  then
    rm -rf -- "$audit_dir"
    return 1
  fi
  rm -rf -- "$audit_dir"
}

join_worker() {
  local worker="$1" node="$2" force_rejoin="${3:-0}" started_ms join_cmd
  local -a join_args
  started_ms="$(($(date +%s) * 1000))"
  emit capacity-change started "$node"
  kb_vm_up "$worker"
  kb_ensure_sysctls "$worker"
  kb_ensure_kubelet_node_ip "$worker" "$(kb_vm_ip "$cluster" "$worker")"
  if [ "$force_rejoin" = 1 ]; then
    kb_log "resetting stale kubeadm identity on $worker before rejoin"
    kb_root "$worker" -- kubeadm reset --force
    kb_root "$worker" -- sh -ec \
      'for path in /etc/cni/net.d /var/lib/cni; do [ ! -d "$path" ] || find "$path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; done'
  fi
  if ! kb_root "$worker" -- test -f /etc/kubernetes/kubelet.conf; then
    join_cmd="$(kb_root "$(kb_cluster_vms "$cluster" | awk '{print $1}')" -- kubeadm token create --print-join-command)"
    read -r -a join_args <<<"$join_cmd"
    kb_root "$worker" -- ${join_args[@]+"${join_args[@]}"}
  fi
  kb_ensure_node_cidrs "$cluster"
  emit capacity-change succeeded "$node" "$started_ms"
}

ensure_worker_membership() {
  local worker="$1" node="$2"
  kb_vm_up "$worker"
  kb_ensure_sysctls "$worker"
  kb_ensure_kubelet_node_ip "$worker" "$(kb_vm_ip "$cluster" "$worker")"
  if ! kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null 2>&1; then
    join_worker "$worker" "$node" 1
    return
  fi
  if kubectl --kubeconfig "$kubeconfig" --context "$cluster" wait "node/$node" \
      --for=condition=Ready --timeout="${NODEPOOL_RECOVERY_TIMEOUT:-120s}" >/dev/null 2>&1; then
    return
  fi
  # Never destroy a possibly recoverable interactive Runtime. An operator must
  # checkpoint/delete it before the local provider resets stale kubelet/CNI
  # identity against a rebuilt control plane.
  assert_idle_node "$node"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" delete node "$node" --wait=true
  join_worker "$worker" "$node" 1
}

wait_node_capabilities() {
  local worker="$1" node="$2" started_ms probe
  started_ms="$(($(date +%s) * 1000))"
  emit cni-runtime-ready started "$node"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" -n kube-system wait pod \
    --field-selector "spec.nodeName=$node" -l k8s-app=cilium \
    --for=condition=Ready --timeout="${NODEPOOL_READY_TIMEOUT:-10m}"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" get runtimeclass gvisor >/dev/null
  kb_root "$worker" -- runsc --version >/dev/null
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" wait pod -A \
    --field-selector "spec.nodeName=$node" \
    --for=condition=Ready --timeout="${NODEPOOL_DAEMONSET_TIMEOUT:-10m}" \
    -l 'app.kubernetes.io/name in (cilium,cilium-envoy)' >/dev/null 2>&1 || true
  emit cni-runtime-ready succeeded "$node" "$started_ms"

  started_ms="$(($(date +%s) * 1000))"
  emit workload-convergence started "$node"
  probe="infra-nodepool-probe-$(date +%s)"
  probe_node_selector="$(python3 - "$probe_node_selector" "$node" <<'PY'
import json, sys
selector = json.loads(sys.argv[1])
selector["kubernetes.io/hostname"] = sys.argv[2]
print(json.dumps(selector, separators=(",", ":")))
PY
)"
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" -n default run "$probe" \
    --image="${NODEPOOL_PROBE_IMAGE:-registry.k8s.io/pause:3.10.1}" \
    --restart=Never --overrides="{\"spec\":{\"runtimeClassName\":\"gvisor\",\"nodeSelector\":$probe_node_selector,\"tolerations\":$probe_tolerations}}" >/dev/null
  if ! kubectl --kubeconfig "$kubeconfig" --context "$cluster" -n default wait \
      "pod/$probe" --for=condition=Ready --timeout="${NODEPOOL_PROBE_TIMEOUT:-5m}"; then
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" -n default describe "pod/$probe" >&2 || true
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" -n default delete "pod/$probe" --wait=false >/dev/null 2>&1 || true
    emit workload-convergence failed "$node" "$started_ms"
    return 1
  fi
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" -n default delete "pod/$probe" --wait=true >/dev/null
  emit workload-convergence succeeded "$node" "$started_ms"
}

apply_node_policy() {
  local node="$1" label taint stale_labels stale_taints
  stale_labels="$(kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" -o json \
    | python3 -c '
import json, sys
desired = {item.split("=", 1)[0] for item in sys.argv[1].split()}
labels = json.load(sys.stdin).get("metadata", {}).get("labels", {})
print("\\n".join(key for key in labels if key.endswith(("/node-pool", "/node-role")) and key not in desired))
' "$node_labels"
)"
  stale_taints="$(kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" -o json \
    | python3 -c '
import json, sys
desired = {item.split("=", 1)[0] for item in sys.argv[1].split()}
taints = json.load(sys.stdin).get("spec", {}).get("taints", [])
print("\\n".join(taint["key"] for taint in taints if taint.get("key", "").endswith(("/node-pool", "/node-role")) and taint["key"] not in desired))
' "$node_taints"
)"
  for label in $stale_labels; do
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" label node "$node" "$label-" >/dev/null
  done
  for taint in $stale_taints; do
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" taint node "$node" "$taint-" >/dev/null
  done
  for label in $node_labels; do
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" label node "$node" "$label" --overwrite >/dev/null
  done
  for taint in $node_taints; do
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" taint node "$node" "$taint" --overwrite >/dev/null
  done
}

converge_system_pods_off_runtime_node() {
  local node="$1" namespace pod
  # Required scheduling affinity is ignored after a Pod is running. During an
  # upgrade, recreate only Pods whose own spec declares the stable system role;
  # their controllers then place them on the control-plane/system node.
  while IFS=$'\t' read -r namespace pod; do
    [ -z "$pod" ] || kubectl --kubeconfig "$kubeconfig" --context "$cluster" \
      -n "$namespace" delete "pod/$pod" --wait=true
  done < <(kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pods -A \
    --field-selector "spec.nodeName=$node" -o json | python3 -c '
import json, sys
for pod in json.load(sys.stdin).get("items", []):
    if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
        continue
    if any(owner.get("kind") == "DaemonSet" for owner in pod.get("metadata", {}).get("ownerReferences", [])):
        continue
    if pod.get("spec", {}).get("nodeSelector", {}).get("infra.convee.io/node-role") != "system":
        continue
    meta = pod["metadata"]
    print("{}\t{}".format(meta.get("namespace"), meta.get("name")))')
}

stop_worker() {
  local worker="$1" node="$2" started_ms
  started_ms="$(($(date +%s) * 1000))"
  emit preflight started "$node"
  assert_idle_node "$node"
  pod_has_persistent_or_ephemeral_data "$node"
  emit preflight succeeded "$node" "$started_ms"
  if kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null 2>&1; then
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" cordon "$node"
    if ! kubectl --kubeconfig "$kubeconfig" --context "$cluster" drain "$node" \
        --ignore-daemonsets --timeout="${drain_timeout}s"; then
      kubectl --kubeconfig "$kubeconfig" --context "$cluster" uncordon "$node" >/dev/null 2>&1 || true
      emit capacity-change failed "$node" "$started_ms"
      return 1
    fi
  fi
  if [ "$(kb_vm_state "$worker")" = Running ]; then
    kb_vm_down "$worker"
  fi
  kubectl --kubeconfig "$kubeconfig" --context "$cluster" delete node "$node" --wait=false >/dev/null 2>&1 || true
  emit capacity-change succeeded "$node" "$started_ms"
  started_ms="$(($(date +%s) * 1000))"
  emit workload-convergence started "$node"
  for _ in $(seq 1 120); do
    if ! kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null 2>&1; then
    emit workload-convergence failed "$node" "$started_ms"
    echo "node object remained after scale-down: $node" >&2
    return 1
  fi
  unschedulable="$(kubectl --kubeconfig "$kubeconfig" --context "$cluster" get pods -A -o json \
    | python3 -c 'import json,sys; print(sum(any(c.get("type") == "PodScheduled" and c.get("status") == "False" and c.get("reason") == "Unschedulable" for c in p.get("status",{}).get("conditions",[])) for p in json.load(sys.stdin).get("items",[])))')"
  if [ "$unschedulable" -ne 0 ]; then
    emit workload-convergence failed "$node" "$started_ms"
    echo "scale-down left $unschedulable unschedulable pod(s)" >&2
    return 1
  fi
  emit workload-convergence succeeded "$node" "$started_ms"
}

for index in "${!worker_vm_array[@]}"; do
  worker="${worker_vm_array[$index]}"
  node="${worker_node_array[$index]}"
  if [ "$index" -lt "$desired" ]; then
    ensure_worker_membership "$worker" "$node"
    phase_started_ms="$(($(date +%s) * 1000))"
    emit node-ready started "$node"
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" wait \
      "node/$node" --for=condition=Ready --timeout="${NODEPOOL_READY_TIMEOUT:-10m}"
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" uncordon "$node" >/dev/null 2>&1 || true
    emit node-ready succeeded "$node" "$phase_started_ms"
    apply_node_policy "$node"
    converge_system_pods_off_runtime_node "$node"
    assert_runtime_boundary "$node"
    wait_node_capabilities "$worker" "$node"
  elif [ "$(kb_vm_state "$worker")" = Running ] \
      || kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null 2>&1; then
    stop_worker "$worker" "$node"
  fi
done

verification_started_ms="$(($(date +%s) * 1000))"
emit verification started
for index in "${!worker_node_array[@]}"; do
  node="${worker_node_array[$index]}"
  if [ "$index" -lt "$desired" ]; then
    kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null
    assert_runtime_boundary "$node"
  else
    ! kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node "$node" >/dev/null 2>&1 || {
      emit verification failed "$node" "$verification_started_ms"
      echo "scaled-down Node still exists: $node" >&2
      exit 1
    }
  fi
done
emit verification succeeded "" "$verification_started_ms"
