from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def shell(script: str, **environment: str) -> dict[str, str]:
    """Run `script` with scripts/common.sh sourced and return its `k=v` output.

    Nothing here stubs the resolvers: it is the real common.sh, the real
    kubeadm-bootstrap.sh and the real kubeadm_profile.py, because the divergence
    this pins only existed between those three files.
    """
    result = subprocess.run(
        ["bash", "-c", f'source "{ROOT}/scripts/common.sh"\n{script}'],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, **environment},
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


class StateDirectoryTests(unittest.TestCase):
    """One state directory, resolved once.

    `kubeadm_profile.py` composed `.infra/kubeconfig-<cluster>` while common.sh
    composed `.state/kubeconfig-<cluster>`. bootstrap.sh created the second and
    chmod-ed it right after the first had been written, so `set -euo pipefail`
    aborted `make bootstrap` with the cluster already up and Argo CD not yet
    installed. CI never starts a VM, so nothing caught it; the only trace was
    someone adding `.infra/` to .gitignore after watching it appear.
    """

    def test_bootstrap_chmods_the_file_kb_ensure_cluster_writes(self) -> None:
        values = shell(
            'printf "dir=%s\\nfrom_common=%s\\nfrom_profile=%s\\n" '
            '"$infra_dir" "$infra_kubeconfig" "$(kb_kubeconfig_path "$infra_cluster")"'
        )
        self.assertEqual(values["from_profile"], values["from_common"])
        self.assertEqual(values["dir"], str(Path(values["from_common"]).parent))
        self.assertEqual(str(ROOT / ".state"), values["dir"])

    def test_the_override_moves_both_resolvers_together(self) -> None:
        values = shell(
            'printf "dir=%s\\nfrom_common=%s\\nfrom_profile=%s\\n" '
            '"$infra_dir" "$infra_kubeconfig" "$(kb_kubeconfig_path "$infra_cluster")"',
            INFRA_DIR="/tmp/infra-state-probe",
        )
        self.assertEqual("/tmp/infra-state-probe", values["dir"])
        self.assertEqual("/tmp/infra-state-probe/kubeconfig-infra", values["from_common"])
        self.assertEqual(values["from_profile"], values["from_common"])

    def test_the_management_cluster_name_reaches_the_kubeconfig_name(self) -> None:
        # common.sh used to spell the file name itself with `infra` baked in, so
        # INFRA_CLUSTER moved every kubectl call in the file except the path of
        # the kubeconfig they all read. Re-spelling the path instead of deriving
        # it looks harmless until this knob is used.
        values = shell(
            'printf "from_common=%s\\nfrom_profile=%s\\n" '
            '"$infra_kubeconfig" "$(kb_kubeconfig_path "$infra_cluster")"',
            INFRA_CLUSTER="workload",
        )
        self.assertTrue(values["from_common"].endswith("/kubeconfig-workload"))
        self.assertEqual(values["from_profile"], values["from_common"])

    def test_an_explicit_kubeconfig_override_still_wins(self) -> None:
        values = shell(
            'printf "from_common=%s\\n" "$infra_kubeconfig"',
            INFRA_KUBECONFIG="/tmp/infra-state-probe/elsewhere.conf",
        )
        self.assertEqual("/tmp/infra-state-probe/elsewhere.conf", values["from_common"])

    def test_workload_kubeconfig_resolves_into_the_same_directory(self) -> None:
        values = shell(
            'printf "dir=%s\\nworkload=%s\\n" "$infra_dir" "$(kb_kubeconfig_path workload)"'
        )
        self.assertEqual(f"{values['dir']}/kubeconfig-workload", values["workload"])

    def test_the_shipped_node_pool_points_at_that_same_file(self) -> None:
        # Third place the path was written by hand. It happened to be right
        # while the profile was wrong, which is why nothing looked suspicious.
        pool = yaml.safe_load(
            (ROOT / "examples" / "node-pools" / "local-workload.yaml").read_text(
                encoding="utf-8"
            )
        )
        declared = pool["spec"]["provider"]["parameters"]["kubeconfig"]
        values = shell('printf "workload=%s\\n" "$(kb_kubeconfig_path workload)"')
        self.assertEqual(str(ROOT / declared), values["workload"])

    def test_the_abandoned_state_directory_is_gone_from_every_tracked_file(self) -> None:
        # Absence assertions rot: this one has to name the string that would
        # reappear, not one that no longer exists anywhere.
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8").rstrip("\0").split("\0")
        offenders = []
        for name in tracked:
            if name == "tests/test_state_directory.py":
                continue
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
            if ".infra/" in text:
                offenders.append(name)
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
