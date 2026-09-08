"""Exercise publication against a temporary real Git remote and a fake cluster."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class RenderedPublisherTests(unittest.TestCase):
    def test_publishes_git_and_registers_only_a_watching_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            shutil.copytree(ROOT / "scripts", source / "scripts")
            (source / "VERSION").write_text("0.1.0\n")
            python_dir = source / ".venv/bin"
            python_dir.mkdir(parents=True)
            (python_dir / "python").write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
            (python_dir / "python").chmod(0o755)
            remote, seed = root / "remote.git", root / "seed"
            def git(*args, cwd=root):
                return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True)
            git("init", "--bare", "--initial-branch=main", str(remote))
            git("clone", str(remote), str(seed))
            git("config", "user.name", "Test", cwd=seed)
            git("config", "user.email", "test@example.invalid", cwd=seed)
            (seed / "README").write_text("test remote\n")
            git("add", "README", cwd=seed)
            git("commit", "-m", "initial", cwd=seed)
            git("push", "origin", "main", cwd=seed)
            manifest = root / "rendered.yaml"
            manifest.write_text(yaml.safe_dump({
                "apiVersion": "argoproj.io/v1alpha1", "kind": "ApplicationSet",
                "metadata": {"name": "test-set", "namespace": "delivery-system"},
                "spec": {},
            }))
            binary = root / "bin"
            binary.mkdir()
            applied = root / "applied.yaml"
            kubectl = binary / "kubectl"
            kubectl.write_text(f'#!{sys.executable}\n' + '''import os, sys
from pathlib import Path
args = sys.argv[1:]
if "rollout" in args:
    raise SystemExit(0)
if args[-3:] == ["apply", "-f", "-"]:
    Path(os.environ["TEST_APPLIED"]).write_text(sys.stdin.read())
    raise SystemExit(0)
raise SystemExit("unexpected cluster operation")
''')
            kubectl.chmod(0o755)
            env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                   "INFRA_DIR": str(root / "state"), "INFRA_GIT_URL": str(remote),
                   "INFRA_GIT_CLUSTER_URL": "https://git.example.invalid/deployments.git",
                   "ARGOCD_NAMESPACE": "delivery-system", "INFRA_ROOT_PROJECT": "delivery",
                   "TEST_APPLIED": str(applied)}
            result = subprocess.run([str(source / "scripts/publish-rendered.sh"), "demo", str(manifest)],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            parent = yaml.safe_load(applied.read_text())
            self.assertEqual("Application", parent["kind"])
            self.assertEqual("infra-rendered-demo", parent["metadata"]["name"])
            self.assertEqual("delivery-system", parent["metadata"]["namespace"])
            self.assertEqual("delivery", parent["spec"]["project"])
            self.assertEqual("rendered/demo", parent["spec"]["source"]["path"])
            self.assertEqual("main", parent["spec"]["source"]["targetRevision"])
            self.assertEqual(env["INFRA_GIT_CLUSTER_URL"], parent["spec"]["source"]["repoURL"])
            self.assertEqual({"prune": False, "selfHeal": True}, parent["spec"]["syncPolicy"]["automated"])
            self.assertNotIn("finalizers", parent["metadata"])
            self.assertEqual(manifest.read_text(), git("--git-dir", str(remote), "show", "main:rendered/demo/all.yaml"))
            # A namespace mismatch must fail before changing the Git revision.
            before = git("--git-dir", str(remote), "rev-parse", "main")
            env["ARGOCD_NAMESPACE"] = "wrong-namespace"
            result = subprocess.run([str(source / "scripts/publish-rendered.sh"), "demo", str(manifest)],
                                    env=env, text=True, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("namespace must match", result.stderr)
            self.assertEqual(before, git("--git-dir", str(remote), "rev-parse", "main"))
