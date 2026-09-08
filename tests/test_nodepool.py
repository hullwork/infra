from __future__ import annotations

import json
import os
import importlib.util
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "nodepool.py"
EXAMPLE = ROOT / "examples" / "node-pools" / "local-workload.yaml"

SPEC = importlib.util.spec_from_file_location("infra_nodepool", CLI)
assert SPEC and SPEC.loader
NODEPOOL = importlib.util.module_from_spec(SPEC)
sys.dont_write_bytecode = True
SPEC.loader.exec_module(NODEPOOL)


def _process_exists(pid: int, timeout: float = 5.0) -> bool:
    """Whether ``pid`` is still alive, allowing a moment for it to be reaped."""
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        time.sleep(0.05)
    return True


def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


class NodePoolTests(unittest.TestCase):
    def test_local_example_validates_and_has_staged_plan(self) -> None:
        result = run_cli("validate", "--pool", str(EXAMPLE))
        self.assertIn("validated NodePool workload-workers", result.stdout)
        plan = yaml.safe_load(run_cli("plan", "--pool", str(EXAMPLE), "--desired", "0").stdout)
        self.assertEqual("NodePoolPlan", plan["kind"])
        self.assertEqual(
            [
                "preflight",
                "capacity-change",
                "node-ready",
                "cni-runtime-ready",
                "workload-convergence",
                "verification",
            ],
            plan["spec"]["phases"],
        )
        self.assertFalse(plan["spec"]["scaleDown"]["allowLocalPersistentVolumes"])
        self.assertTrue(plan["spec"]["scaleDown"]["retainProviderDisks"])

    def test_invalid_capacity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = yaml.safe_load(EXAMPLE.read_text())
            pool["spec"]["capacity"].update(min=1, desired=0, max=1)
            path = Path(temporary) / "pool.yaml"
            path.write_text(yaml.safe_dump(pool, sort_keys=False))
            result = run_cli("validate", "--pool", str(path), check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("min <= desired <= max", result.stderr)

    def test_metric_label_injection_is_rejected_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = yaml.safe_load(EXAMPLE.read_text())
            pool["metadata"]["name"] = 'bad"}\nforged_metric 1'
            path = Path(temporary) / "pool.yaml"
            path.write_text(yaml.safe_dump(pool, sort_keys=False))
            result = run_cli("validate", "--pool", str(path), check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("schema violation", result.stderr)

    def test_event_log_rotation_and_atomic_metrics_output_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            event_log = Path(temporary) / "events.jsonl"
            event_log.write_bytes(b"x" * (5 * 1024 * 1024))
            pool = yaml.safe_load(EXAMPLE.read_text())
            NODEPOOL.append_event(
                event_log,
                NODEPOOL.event(pool, "reconcile", "succeeded", durationMs=1),
            )
            metrics = NODEPOOL.write_prometheus(event_log, pool)
            metrics_text = metrics.read_text()
            leftovers = list(Path(temporary).glob(".*.tmp"))
            rotated_size = Path(str(event_log) + ".1").stat().st_size
        self.assertEqual(5 * 1024 * 1024, rotated_size)
        self.assertIn("infra_nodepool_phase_duration_seconds", metrics_text)
        self.assertEqual([], leftovers)

    def test_local_max_cannot_exceed_declared_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = yaml.safe_load(EXAMPLE.read_text())
            pool["spec"]["capacity"].update(desired=2, max=2)
            path = Path(temporary) / "pool.yaml"
            path.write_text(yaml.safe_dump(pool, sort_keys=False))
            result = run_cli("validate", "--pool", str(path), check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("exceeds declared provider workers", result.stderr)

    def test_apply_invokes_provider_and_records_machine_readable_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = yaml.safe_load(EXAMPLE.read_text())
            event_log = root / "events.jsonl"
            pool["spec"]["observability"]["eventLog"] = str(event_log)
            pool["spec"]["provider"]["parameters"]["kubeconfig"] = str(root / "missing-kubeconfig")
            pool_path = root / "pool.yaml"
            pool_path.write_text(yaml.safe_dump(pool, sort_keys=False))
            provider = root / "provider.sh"
            provider.write_text("#!/usr/bin/env bash\nset -eu\n[ \"$1\" = reconcile ]\n[ \"$5\" = 1 ]\n")
            provider.chmod(0o755)
            run_cli(
                "apply", "--pool", str(pool_path), "--desired", "1",
                "--provider-command", str(provider),
            )
            events = [line for line in event_log.read_text().splitlines() if line]
            metrics = Path(str(event_log) + ".prom").read_text()
        self.assertEqual(2, len(events))
        self.assertIn('"status":"started"', events[0])
        self.assertIn('"status":"succeeded"', events[1])
        self.assertIn("# TYPE infra_nodepool_reconcile_failed gauge", metrics)
        self.assertIn("infra_nodepool_reconcile_failed", metrics)
        self.assertIn('phase="reconcile",status="succeeded"', metrics)

    def _apply_with_provider(
        self, root: Path, script: str, **spec_overrides
    ) -> tuple[subprocess.CompletedProcess[str], list[dict], str]:
        """Run `apply` against a throwaway provider and return (result, events, metrics)."""
        pool = yaml.safe_load(EXAMPLE.read_text())
        event_log = root / "events.jsonl"
        pool["spec"]["observability"]["eventLog"] = str(event_log)
        pool["spec"]["observability"].update(spec_overrides)
        pool["spec"]["provider"]["parameters"]["kubeconfig"] = str(root / "missing-kubeconfig")
        pool_path = root / "pool.yaml"
        pool_path.write_text(yaml.safe_dump(pool, sort_keys=False))
        provider = root / "provider.sh"
        provider.write_text(script)
        provider.chmod(0o755)
        result = run_cli(
            "apply", "--pool", str(pool_path), "--desired", "1",
            "--provider-command", str(provider), check=False,
        )
        events = [
            json.loads(line)
            for line in event_log.read_text().splitlines()
            if line
        ]
        return result, events, Path(str(event_log) + ".prom").read_text()

    def test_a_hung_provider_is_killed_at_the_declared_phase_timeout(self) -> None:
        """🔴 phaseTimeoutSeconds was required by the schema and read by nothing.

        The compiler can only bound the whole run -- the phases are inside the
        adapter -- but without any bound a provider that hangs hangs the
        reconcile forever, and the terminal event that both the JSONL log and
        the Prometheus gauge are built around is never written.  An operator
        watching either sees a reconcile permanently in "started".

        This drives ``reconcile`` directly rather than the CLI because the
        schema floors the field at 30 seconds; going through ``load_pool``
        would make this case cost half a minute of wall clock to prove a
        one-line behaviour.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = yaml.safe_load(EXAMPLE.read_text())
            event_log = root / "events.jsonl"
            pool["spec"]["observability"]["eventLog"] = str(event_log)
            pool["spec"]["observability"]["phaseTimeoutSeconds"] = 1
            # Not local-lima-kubeadm, so reconcile does not try to publish the
            # metrics ConfigMap through kubectl on a machine with no cluster.
            pool["spec"]["provider"]["type"] = "external"
            provider = root / "provider.sh"
            # The adapter backgrounds a child and records its pid, which is what
            # the real one does with limactl and kubectl.
            child_pid_file = root / "child.pid"
            provider.write_text(
                "#!/usr/bin/env bash\n"
                "sleep 600 &\n"
                f'echo $! > "{child_pid_file}"\n'
                "wait\n"
            )
            provider.chmod(0o755)
            started = time.monotonic()
            # The timeout assertion starts once the fixture has actually
            # spawned its child. Process launch can take >1s on a busy host;
            # timing that race did not test descendant cleanup at all.
            real_popen = subprocess.Popen

            def start_fixture(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                deadline = time.monotonic() + 10
                while not child_pid_file.exists() or not child_pid_file.read_text().strip():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        NODEPOOL._end_process_group(process)
                        raise AssertionError("provider fixture did not start its child")
                    time.sleep(0.01)
                return process

            with mock.patch.object(NODEPOOL.subprocess, "Popen", side_effect=start_fixture):
                with self.assertRaisesRegex(NODEPOOL.NodePoolError, "phaseTimeoutSeconds"):
                    NODEPOOL.reconcile(root / "pool.yaml", pool, 1, provider)
            elapsed = time.monotonic() - started
            child_pid = int(child_pid_file.read_text().strip())
            events = [
                json.loads(line)
                for line in event_log.read_text().splitlines() if line
            ]
            metrics = Path(str(event_log) + ".prom").read_text()
        # Red if the adapter is killed on its own. subprocess.run(timeout=)
        # ends the adapter and leaves its children running: measured here as an
        # orphaned `sleep 600` that outlived the reconcile and held its stdout
        # pipe open, blocking the test runner's own output for ten minutes.
        self.assertFalse(
            _process_exists(child_pid),
            f"pid {child_pid} outlived the reconcile: the adapter's children "
            "were not in the killed process group",
        )
        # Red if the declared value stops being read: the sleep is 600s, so a
        # missing or hardcoded-larger timeout shows up as wall clock, not as a
        # different message.
        self.assertLess(elapsed, 30, f"the 1s timeout was not the one applied ({elapsed:.1f}s)")
        # The terminal event and the gauge, not just the exception: those are
        # what an operator actually reads, and they were the things missing.
        self.assertEqual(["started", "failed"], [item["status"] for item in events])
        self.assertRegex(metrics, r"infra_nodepool_reconcile_failed\{[^}]*\} 1")

    def test_a_provider_inside_the_timeout_still_succeeds(self) -> None:
        # The other direction: a timeout implemented as "always fail" would pass
        # the case above and break every real reconcile.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, events, metrics = self._apply_with_provider(
                root, "#!/usr/bin/env bash\nexit 0\n", phaseTimeoutSeconds=30,
            )
        self.assertEqual(0, result.returncode)
        self.assertEqual(["started", "succeeded"], [item["status"] for item in events])
        self.assertRegex(metrics, r"infra_nodepool_reconcile_failed\{[^}]*\} 0")

    def test_the_declared_metrics_prefix_reaches_the_metric_names(self) -> None:
        # metricsPrefix was declared in the schema and in the shipped example
        # and read by nothing: setting it renamed no metric and reported no
        # error, so a dashboard built on the declared name found nothing.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _result, _events, metrics = self._apply_with_provider(
                root, "#!/usr/bin/env bash\nexit 0\n", metricsPrefix="acme_pool",
            )
        self.assertIn("acme_pool_reconcile_failed", metrics)
        self.assertIn("acme_pool_phase_duration_seconds", metrics)
        self.assertIn("# TYPE acme_pool_reconcile_failed gauge", metrics)
        # Red if the prefix is prepended rather than substituted, which would
        # emit both names and leave the old dashboard silently working.
        self.assertNotIn("infra_nodepool_", metrics)

    def test_the_default_prefix_still_applies_when_none_is_declared(self) -> None:
        # metricsPrefix is optional in the schema, so its absence must not be a
        # KeyError and must not rename anything.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = yaml.safe_load(EXAMPLE.read_text())
            del pool["spec"]["observability"]["metricsPrefix"]
            event_log = Path(temporary) / "events.jsonl"
            pool["spec"]["observability"]["eventLog"] = str(event_log)
            event_log.write_text(
                '{"phase":"reconcile","status":"succeeded","durationMs":1}\n'
            )
            metrics = NODEPOOL.write_prometheus(event_log, pool).read_text()
        self.assertIn("infra_nodepool_reconcile_failed", metrics)

    def test_shell_provider_is_syntax_valid(self) -> None:
        provider = ROOT / "scripts" / "providers" / "lima-kubeadm-nodepool.sh"
        subprocess.run(
            ["bash", "-n", str(provider)],
            check=True,
        )
        source = provider.read_text(encoding="utf-8")
        self.assertIn("kb_fetch_kubeconfig", source)
        self.assertIn("assert_runtime_boundary", source)
        self.assertIn('labels = parameters["labels"]', source)
        self.assertIn("selector.get(key) == value for key, value in labels.items()", source)
        self.assertIn("assert_idle_node", source)
        self.assertIn("ensure_worker_membership", source)
        self.assertIn("kubeadm reset --force", source)
        self.assertIn("NODEPOOL_RECOVERY_TIMEOUT", source)
        self.assertIn('|| kubectl --kubeconfig "$kubeconfig" --context "$cluster" get node', source)
        self.assertIn("cni-runtime-ready", source)
        self.assertIn("workload-convergence", source)
        self.assertIn("stale_labels", source)
        self.assertIn('endswith(("/node-pool", "/node-role"))', source)
        self.assertIn("stale_taints", source)
        self.assertIn("converge_system_pods_off_runtime_node", source)

    def test_workload_bootstrap_registers_argocd_cluster(self) -> None:
        adapter = (ROOT / "scripts" / "bootstrap-workload.sh").read_text(encoding="utf-8")
        common = (ROOT / "scripts" / "common.sh").read_text(encoding="utf-8")
        self.assertIn("register_argocd_cluster()", common)
        self.assertIn("argocd.argoproj.io/secret-type", common)
        self.assertIn('register_argocd_cluster "$cluster"', adapter)
        self.assertIn("restart_argocd_cluster_cache", adapter)

    def test_preflight_allows_idempotent_management_vm_ports(self) -> None:
        preflight = (ROOT / "scripts" / "preflight.sh").read_text(encoding="utf-8")
        common = (ROOT / "scripts" / "common.sh").read_text(encoding="utf-8")
        self.assertIn("ports are already bound by the running management VM", preflight)
        self.assertIn("cluster_ports_bound_by_running_vms", preflight)
        self.assertIn("cluster_ports_bound_by_running_vms", common)

    def test_workload_preflight_allows_only_existing_cluster_ports(self) -> None:
        bootstrap = (ROOT / "scripts" / "bootstrap-workload.sh").read_text(encoding="utf-8")
        common = (ROOT / "scripts" / "common.sh").read_text(encoding="utf-8")
        self.assertIn("ports are already bound by the running cluster VMs", bootstrap)
        self.assertIn('cluster_ports_bound_by_running_vms "$cluster"', bootstrap)
        self.assertIn('[[ "$(kb_vm_state "$vm")" == "Running" ]] || continue', common)

    def test_cilium_operator_tolerates_bootstrap_node_states(self) -> None:
        installer = (ROOT / "scripts" / "install-cilium-kubeadm.sh").read_text(encoding="utf-8")
        self.assertIn("operator.tolerations[1].key=node.kubernetes.io/not-ready", installer)
        self.assertIn("operator.tolerations[2].key=node.kubernetes.io/unreachable", installer)

    def test_local_bootstrap_scripts_parse(self) -> None:
        scripts = sorted((ROOT / "scripts").rglob("*.sh"))
        self.assertTrue(scripts)
        # bash -n file1 file2 treats file2 as an argument, not another script.
        for script in scripts:
            with self.subTest(script=script.name):
                subprocess.run(["bash", "-n", str(script)], check=True)

    def test_local_profile_is_generic_management_and_workload(self) -> None:
        profile = ROOT / "scripts" / "lib" / "kubeadm_profile.py"
        infra = subprocess.run(
            [sys.executable, str(profile), "show", "infra"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        workload = subprocess.run(
            [sys.executable, str(profile), "show", "workload"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("vms=infra ", infra)
        self.assertIn("kubeconfig=kubeconfig-infra", infra)
        self.assertIn("vms=workload,workload-w1 ", workload)
        self.assertIn("kubeconfig=kubeconfig-workload", workload)
        self.assertNotEqual(
            next(line for line in infra.splitlines() if " pod=" in line).split(" pod=", 1)[1],
            next(line for line in workload.splitlines() if " pod=" in line).split(" pod=", 1)[1],
        )
        for name in ("infra", "workload", "workload-w1"):
            self.assertTrue((ROOT / "lima" / f"{name}.yaml").is_file())

    def test_consumed_vm_definitions_use_upstream_kubernetes_apt_repository(self) -> None:
        # kubeadm-bootstrap.sh runs `limactl start <vm> lima/<vm>.yaml`, so the
        # yaml files are what the package repository is actually read from. The
        # first version of this test read only the renderer template: with all
        # three yaml files pointing at the wrong repository and the template
        # correct, the whole suite stayed green (verified by mutation).
        consumed = sorted((ROOT / "lima").glob("*.yaml"))
        self.assertGreaterEqual(len(consumed), 3, "no VM definitions to check")
        for path in [*consumed, ROOT / "scripts" / "lib" / "kubeadm_profile.py"]:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("https://pkgs.k8s.io/core:/stable:/v1.36/deb/Release.key", text)
                self.assertIn("https://pkgs.k8s.io/core:/stable:/v1.36/deb/ /", text)
                self.assertNotIn("pkgs.k8s.io/management:", text)

    def test_vm_definitions_match_their_renderer_byte_for_byte(self) -> None:
        # The template and its four outputs were being edited by hand, once per
        # file, and nothing regenerated or compared them -- `render` has no
        # caller in any script, Makefile target or workflow. Comparing bytes
        # here makes the template the source of truth it claims to be, so a
        # change applied to only some of the files fails instead of silently
        # producing a VM configured differently from the one that was reviewed.
        renderer = ROOT / "scripts" / "lib" / "kubeadm_profile.py"
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(
                [sys.executable, str(renderer), "render", "--out", temporary],
                check=True,
                capture_output=True,
                text=True,
            )
            rendered = {path.name: path.read_bytes() for path in Path(temporary).glob("*.yaml")}
        tracked = {path.name: path.read_bytes() for path in (ROOT / "lima").glob("*.yaml")}
        self.assertEqual(sorted(tracked), sorted(rendered))
        for name in sorted(tracked):
            with self.subTest(name=name):
                self.assertEqual(
                    rendered[name].decode("utf-8"),
                    tracked[name].decode("utf-8"),
                    f"lima/{name} has drifted from its renderer; regenerate with "
                    f"`python3 scripts/lib/kubeadm_profile.py render --out lima`",
                )


if __name__ == "__main__":
    unittest.main()
