from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "lib" / "host-workload-guard.sh"
# The rule, not a hand-kept list: anything that brings a local cluster up is an
# entrypoint and has to take the lock. A new scripts/bootstrap-*.sh is covered
# the moment it is added.
ENTRYPOINTS = sorted(ROOT.glob("scripts/bootstrap*.sh"))


def bash(script: str, **environment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash" if sys.platform == "darwin" else "bash", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, **environment},
    )


class HostWorkloadLockTests(unittest.TestCase):
    """The lock has to be held on both sides or it is not mutual exclusion.

    A commit whose subject was about package repositories also deleted the
    seven-line wrapper from one entrypoint without mentioning it. The other
    entrypoint kept acquiring the lock, so the failure mode was not "no lock"
    but "one process waits, the other walks straight through" -- and the comment
    at the top of the guard still claimed every entrypoint used it.
    """

    def test_every_local_cluster_entrypoint_takes_the_lock(self) -> None:
        self.assertGreaterEqual(len(ENTRYPOINTS), 2, "entrypoint glob matched nothing")
        for entrypoint in ENTRYPOINTS:
            with self.subTest(entrypoint=entrypoint.name):
                text = entrypoint.read_text(encoding="utf-8")
                self.assertIn('if [ "${INFRA_BOOTSTRAP_GUARDED:-0}" != "1" ]; then', text)
                self.assertIn("infra_with_host_workload_lock", text)
                self.assertIn("export INFRA_BOOTSTRAP_GUARDED=1", text)

    def test_the_lock_actually_excludes_a_second_holder(self) -> None:
        # Behavioural, not a string match: hold the lock and try to take it
        # again from a nested process.
        with tempfile.TemporaryDirectory() as temporary:
            result = bash(
                f'source "{GUARD}"\n'
                'infra_with_host_workload_lock outer bash -c '
                f"'source \"{GUARD}\"; infra_with_host_workload_lock inner true'",
                INFRA_HOST_WORKLOAD_LOCK_DIR=f"{temporary}/lock",
                INFRA_ALLOW_HOST_PRESSURE="1",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("another infra host-heavy operation is active", result.stderr)
            self.assertIn("label=outer", result.stderr)

            # ... and the EXIT trap gives it back, otherwise one aborted run
            # would wedge the host until someone deleted the directory by hand.
            second = bash(
                f'source "{GUARD}"\ninfra_with_host_workload_lock later true',
                INFRA_HOST_WORKLOAD_LOCK_DIR=f"{temporary}/lock",
                INFRA_ALLOW_HOST_PRESSURE="1",
            )
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertFalse(Path(temporary, "lock").exists())

    def test_the_headroom_gate_can_still_refuse(self) -> None:
        # A gate nobody has ever seen fire is indistinguishable from a gate that
        # cannot fire. Drive one of its three limits to a value the host cannot
        # satisfy and require the refusal.
        with tempfile.TemporaryDirectory() as temporary:
            result = bash(
                f'source "{GUARD}"\ninfra_with_host_workload_lock probe true',
                INFRA_HOST_WORKLOAD_LOCK_DIR=f"{temporary}/lock",
                INFRA_MIN_HOST_DISK_KB="999999999999",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("host disk headroom too low", result.stderr)
            self.assertIn("heavy host operation blocked", result.stderr)
            self.assertFalse(Path(temporary, "lock").exists())

    def test_failed_command_releases_lock_under_nounset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = bash(
                f'set -eu; source "{GUARD}"; infra_with_host_workload_lock probe false',
                INFRA_HOST_WORKLOAD_LOCK_DIR=f"{temporary}/lock",
                INFRA_ALLOW_HOST_PRESSURE="1",
            )
            self.assertEqual(1, result.returncode, result.stderr)
            self.assertFalse(Path(temporary, "lock").exists())

    def test_the_bypass_is_explicit_and_announces_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = bash(
                f'source "{GUARD}"\ninfra_with_host_workload_lock probe true',
                INFRA_HOST_WORKLOAD_LOCK_DIR=f"{temporary}/lock",
                INFRA_MIN_HOST_DISK_KB="999999999999",
                INFRA_ALLOW_HOST_PRESSURE="1",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("host pressure gate explicitly bypassed", result.stderr)

    def test_the_load_limit_keeps_its_gawk_safe_variable_name(self) -> None:
        # `load` is a gawk built-in: `awk -v load=...` aborts, and an aborting
        # awk was read as "not over the limit", so this gate passed on every
        # Linux host. There is no injection point to drive load from a test, so
        # the name itself is what gets pinned.
        invocations = [
            line for line in GUARD.read_text(encoding="utf-8").splitlines()
            if "awk -v" in line and not line.lstrip().startswith("#")
        ]
        self.assertTrue(invocations, "no awk invocation left to check")
        for line in invocations:
            with self.subTest(line=line.strip()):
                self.assertNotIn("-v load=", line)
        self.assertTrue(any("-v load1=" in line for line in invocations))

    def test_the_guard_comment_matches_the_enforced_entrypoint_set(self) -> None:
        guard = GUARD.read_text(encoding="utf-8")
        self.assertIn("All local cluster entrypoints use the same lock", guard)
        self.assertIn("tests/test_host_workload_lock.py", guard)


if __name__ == "__main__":
    unittest.main()
