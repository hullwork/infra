#!/usr/bin/env python3
"""Validate and reconcile product-neutral node-pool declarations."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "v1alpha1" / "node-pool.schema.json"


class NodePoolError(ValueError):
    pass


def load_pool(path: Path) -> dict[str, Any]:
    try:
        pool = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise NodePoolError(f"{path}: cannot read NodePool: {exc}") from exc
    if not isinstance(pool, dict):
        raise NodePoolError(f"{path}: expected one YAML object")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(pool),
        key=lambda error: ".".join(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "document"
        raise NodePoolError(f"{path}: schema violation at {location}: {error.message}")
    capacity = pool["spec"]["capacity"]
    if not capacity["min"] <= capacity["desired"] <= capacity["max"]:
        raise NodePoolError(f"{path}: capacity must satisfy min <= desired <= max")
    provider = pool["spec"]["provider"]
    if provider["type"] == "local-lima-kubeadm":
        parameters = provider.get("parameters", {})
        workers = parameters.get("workers")
        if not isinstance(workers, list) or not workers or not all(
            isinstance(worker, dict)
            and isinstance(worker.get("vm"), str) and worker["vm"]
            and isinstance(worker.get("node"), str) and worker["node"]
            for worker in workers
        ):
            raise NodePoolError(f"{path}: local-lima-kubeadm requires parameters.workers")
        if len({worker["vm"] for worker in workers}) != len(workers) or len(
            {worker["node"] for worker in workers}
        ) != len(workers):
            raise NodePoolError(f"{path}: provider worker VM and Node names must be unique")
        if not isinstance(parameters.get("kubeconfig"), str) or not parameters["kubeconfig"]:
            raise NodePoolError(f"{path}: local-lima-kubeadm requires parameters.kubeconfig")
        labels = parameters.get("labels")
        taints = parameters.get("taints")
        if not isinstance(labels, dict) or not labels or not all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in labels.items()
        ):
            raise NodePoolError(f"{path}: local-lima-kubeadm requires parameters.labels")
        if not isinstance(taints, list) or not taints or not all(
            isinstance(taint, str) and taint.endswith(":NoSchedule") for taint in taints
        ):
            raise NodePoolError(f"{path}: local-lima-kubeadm requires NoSchedule parameters.taints")
        if capacity["max"] > len(workers):
            raise NodePoolError(f"{path}: capacity.max exceeds declared provider workers")
    return pool


def event(pool: dict[str, Any], phase: str, status: str, **fields: Any) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "timestampUnixMs": int(time.time() * 1000),
        "pool": pool["metadata"]["name"],
        "cluster": pool["spec"]["cluster"],
        "provider": pool["spec"]["provider"]["type"],
        "phase": phase,
        "status": status,
        **fields,
    }


def append_event(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = 5 * 1024 * 1024
    if path.exists() and path.stat().st_size >= max_bytes:
        rotated = path.with_suffix(path.suffix + ".1")
        os.replace(path, rotated)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")


def write_prometheus(path: Path, pool: dict[str, Any]) -> Path:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    labels = {
        "pool": pool["metadata"]["name"],
        "cluster": pool["spec"]["cluster"],
        "provider": pool["spec"]["provider"]["type"],
    }
    def prometheus_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    label_text = ",".join(
        f'{key}="{prometheus_escape(value)}"' for key, value in labels.items()
    )
    # metricsPrefix was in node-pool.schema.json and in the shipped example and
    # was read by nothing: setting it renamed no metric and reported no error,
    # so a dashboard built on the declared name found an empty series.
    prefix = pool["spec"]["observability"].get("metricsPrefix", "infra_nodepool")
    reconciles = [item for item in events if item.get("phase") == "reconcile"]
    failed = int(bool(reconciles) and reconciles[-1].get("status") == "failed")
    lines = [
        f"# HELP {prefix}_reconcile_failed Whether the latest provider reconciliation failed.",
        f"# TYPE {prefix}_reconcile_failed gauge",
        f"{prefix}_reconcile_failed{{{label_text}}} {failed}",
        f"# HELP {prefix}_phase_duration_seconds Last completed phase duration.",
        f"# TYPE {prefix}_phase_duration_seconds gauge",
    ]
    latest: dict[tuple[str, str], float] = {}
    for item in events:
        if "durationMs" in item:
            latest[(item.get("phase", "unknown"), item.get("status", "unknown"))] = (
                float(item["durationMs"]) / 1000
            )
    for (phase, status), duration in sorted(latest.items()):
        # Escaped like every other label: phase and status reach this file from
        # the event log, which the provider adapter also appends to with values
        # of its own choosing. One quote in a phase name is a metrics file the
        # textfile collector rejects whole.
        lines.append(
            f"{prefix}_phase_duration_seconds{{{label_text},"
            f'phase="{prometheus_escape(phase)}",status="{prometheus_escape(status)}"}} {duration}'
        )
    metrics_path = Path(str(path) + ".prom")
    temporary = metrics_path.with_name(f".{metrics_path.name}.{os.getpid()}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, metrics_path)
    return metrics_path


def plan(pool: dict[str, Any], desired: int) -> dict[str, Any]:
    capacity = pool["spec"]["capacity"]
    if not capacity["min"] <= desired <= capacity["max"]:
        raise NodePoolError(
            f"desired {desired} is outside [{capacity['min']}, {capacity['max']}]"
        )
    return {
        "apiVersion": "infra.convee.io/v1alpha1",
        "kind": "NodePoolPlan",
        "metadata": {"name": pool["metadata"]["name"]},
        "spec": {
            "cluster": pool["spec"]["cluster"],
            "provider": pool["spec"]["provider"],
            "desired": desired,
            "scaleDown": pool["spec"]["scaleDown"],
            "phases": [
                "preflight",
                "capacity-change",
                "node-ready",
                "cni-runtime-ready",
                "workload-convergence",
                "verification",
            ],
        },
    }


def provider_command(pool: dict[str, Any], override: Path | None) -> Path:
    if override:
        return override
    if pool["spec"]["provider"]["type"] == "local-lima-kubeadm":
        return ROOT / "scripts" / "providers" / "lima-kubeadm-nodepool.sh"
    raise NodePoolError(
        "cloud/external providers are controller-owned; pass --provider-command to an audited adapter"
    )


def _end_process_group(process: subprocess.Popen[Any], grace_seconds: float = 5.0) -> None:
    """SIGTERM the adapter's whole process group, then SIGKILL what is left."""
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signal_number)
        except (ProcessLookupError, PermissionError):
            break
        try:
            process.wait(timeout=grace_seconds)
            break
        except subprocess.TimeoutExpired:
            continue
    with contextlib.suppress(subprocess.TimeoutExpired):
        # Reap the adapter itself so it does not linger as a zombie; the group
        # signal above is what deals with its children.
        process.wait(timeout=grace_seconds)


def reconcile(pool_path: Path, pool: dict[str, Any], desired: int, command: Path) -> None:
    if not command.is_file() or not command.stat().st_mode & 0o111:
        raise NodePoolError(f"provider command is not executable: {command}")
    event_path = Path(pool["spec"]["observability"]["eventLog"])
    if not event_path.is_absolute():
        event_path = ROOT / event_path
    # The phases run inside the provider adapter, so the compiler can only bound
    # the whole run; phaseTimeoutSeconds is that ceiling. It was required by
    # node-pool.schema.json, shipped in the example, and read by nothing: a
    # provider that hung -- on a VM that never boots, an API server that never
    # answers -- hung here forever, and the "failed" event that the event log
    # and the Prometheus gauge are both built around was never written. An
    # operator watching either one saw a reconcile still in "started".
    timeout_seconds = pool["spec"]["observability"]["phaseTimeoutSeconds"]
    started = time.monotonic()
    append_event(event_path, event(pool, "reconcile", "started", desired=desired))
    timed_out = False
    returncode: int | None = None
    # start_new_session so the adapter and everything it starts share one
    # process group. Killing the adapter alone is not enough: it runs limactl
    # and kubectl children, and those keep going -- a half-finished VM
    # operation still running unattended after this process has already
    # declared the reconcile failed, which is worse than the hang it replaces.
    process = subprocess.Popen(
        [str(command), "reconcile", "--pool", str(pool_path), "--desired", str(desired)],
        cwd=ROOT,
        text=True,
        start_new_session=True,
    )
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _end_process_group(process)
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    status = "succeeded" if returncode == 0 else "failed"
    append_event(
        event_path,
        event(pool, "reconcile", status, desired=desired, durationMs=duration_ms),
    )
    metrics_path = write_prometheus(event_path, pool)
    if pool["spec"]["provider"]["type"] == "local-lima-kubeadm":
        parameters = pool["spec"]["provider"]["parameters"]
        kubeconfig = Path(parameters["kubeconfig"])
        if not kubeconfig.is_absolute():
            kubeconfig = ROOT / kubeconfig
        subprocess.run(
            [
                str(ROOT / "scripts" / "nodepool-metrics.sh"), "publish",
                "--metrics", str(metrics_path), "--kubeconfig", str(kubeconfig),
                "--context", pool["spec"]["cluster"],
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
        )
    if timed_out:
        raise NodePoolError(
            f"provider exceeded phaseTimeoutSeconds ({timeout_seconds}s) and was killed"
        )
    if returncode:
        raise NodePoolError(f"provider exited with status {returncode}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--pool", type=Path, required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--pool", type=Path, required=True)
    plan_parser.add_argument("--desired", type=int)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--pool", type=Path, required=True)
    apply_parser.add_argument("--desired", type=int)
    apply_parser.add_argument("--provider-command", type=Path)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        pool = load_pool(args.pool)
        if args.command == "validate":
            print(f"validated NodePool {pool['metadata']['name']}")
            return 0
        desired = args.desired
        if desired is None:
            desired = pool["spec"]["capacity"]["desired"]
        rendered = plan(pool, desired)
        if args.command == "plan":
            yaml.safe_dump(rendered, sys.stdout, sort_keys=False)
            return 0
        reconcile(args.pool.resolve(), pool, desired, provider_command(pool, args.provider_command))
        return 0
    except NodePoolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
