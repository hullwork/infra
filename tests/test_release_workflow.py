from __future__ import annotations

import io
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yaml"
FULL_SHA = re.compile(r"^[^@]+@[0-9a-f]{40}$")
ROOT_REFERENCE = re.compile(r"\$\{?(?:kb_)?infra_root\}?/([A-Za-z0-9_./$${}-]+)")
# Files a consumer must find after unpacking infra-core-*.tar.gz to run
# `make bootstrap`; the rest is derived from the scripts themselves.
REQUIRED_IN_CORE_ARCHIVE = (
    "Makefile",
    "VERSION",
    "requirements-dev.txt",
    "bootstrap/git-server.Dockerfile",
    "bootstrap/git-server.yaml",
    "scripts/bootstrap.sh",
    "scripts/common.sh",
    "scripts/lib/host-workload-guard.sh",
    "scripts/lib/kubeadm-bootstrap.sh",
)


def git_output(*args: str, decode: bool = True):
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return result.decode("utf-8") if decode else result


def shell_array(text: str, name: str) -> list[str]:
    match = re.search(rf"\b{re.escape(name)}=\(([^)]*)\)", text)
    if match is None:
        raise AssertionError(f"{name} array not found in the release workflow")
    return match.group(1).split()


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)

    def test_version_and_changelog_are_in_sync(self) -> None:
        version = (ROOT / "VERSION").read_text().strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
        self.assertIn(f"## [{version}]", (ROOT / "CHANGELOG.md").read_text())

    def test_only_version_tags_trigger_release(self) -> None:
        trigger = self.workflow["on"]
        self.assertEqual({"tags": ["v*"]}, trigger["push"])
        self.assertNotIn("branches", trigger["push"])

    def test_all_actions_are_pinned_to_full_commit_shas(self) -> None:
        steps = self.workflow["jobs"]["release"]["steps"]
        actions = [step["uses"] for step in steps if "uses" in step]
        self.assertGreaterEqual(len(actions), 5)
        for action in actions:
            with self.subTest(action=action):
                self.assertRegex(action.split(" #", 1)[0], FULL_SHA)

    def test_release_has_attestation_permissions_and_main_guard(self) -> None:
        job = self.workflow["jobs"]["release"]
        self.assertEqual("write", job["permissions"]["contents"])
        self.assertEqual("write", job["permissions"]["id-token"])
        self.assertEqual("write", job["permissions"]["attestations"])
        self.assertIn("git cat-file -t", self.text)
        self.assertIn("/compare/${tag_commit}...main", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_release_assets_are_git_archives_with_an_explicit_core_allowlist(self) -> None:
        self.assertIn("core_paths=(", self.text)
        self.assertIn("contract_paths=(", self.text)
        self.assertGreaterEqual(self.text.count("git archive --format=tar"), 2)
        for community_file in (
            "SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SUPPORT.md"
        ):
            self.assertIn(community_file, self.text)
        self.assertNotIn(".state", self.text)
        self.assertNotIn("kubeconfig", self.text)
        self.assertNotIn("tar --exclude", self.text)

    def test_core_paths_entries_all_exist(self) -> None:
        core_paths = shell_array(self.text, "core_paths")
        self.assertGreaterEqual(len(core_paths), 10)
        for entry in core_paths:
            with self.subTest(entry=entry):
                self.assertTrue(
                    (ROOT / entry).exists(),
                    f"core_paths lists {entry!r}, which is not in the repository; "
                    f"`git archive` would fail on it",
                )

    def test_core_paths_covers_every_tracked_top_level_entry(self) -> None:
        # Asserting only that the string "core_paths=(" appears cannot notice a
        # missing directory.  bootstrap/ was absent for exactly that reason, and
        # the unpacked archive could not run `make bootstrap`.
        core_paths = set(shell_array(self.text, "core_paths"))
        tracked_top_level = {
            name.split("/", 1)[0]
            for name in git_output("ls-files").splitlines()
            if name
        }
        # .github/ and .gitignore drive CI in this repository and are not part of
        # what a consumer unpacks; everything else tracked must ship.
        expected = tracked_top_level - {".github", ".gitignore"}
        self.assertEqual(
            set(), expected - core_paths,
            "tracked top-level entries missing from the release core archive",
        )
        self.assertEqual(
            set(), core_paths - tracked_top_level,
            "core_paths lists entries that are not tracked at the top level",
        )

    def test_core_archive_contains_everything_the_entrypoints_dereference(self) -> None:
        # Build the archive the workflow would build and look inside it, instead
        # of trusting the allowlist to be complete.
        core_paths = shell_array(self.text, "core_paths")
        archive = git_output("archive", "--format=tar", "--prefix=release-check/",
                             "HEAD", "--", *core_paths, decode=False)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            members = {
                name[len("release-check/"):]
                for name in tar.getnames()
            }

        # A reference only belongs in the archive if it is tracked, and existence
        # on disk is not that test: the README quick start creates a gitignored
        # `.venv/`, scripts/infra-python.sh dereferences `$infra_root/.venv/bin/
        # python`, and `git archive` can never carry it.  Filtering on `.exists()`
        # therefore failed this gate for everyone who followed the README.
        tracked_paths: set[str] = set()
        for name in git_output("ls-files").splitlines():
            parts = name.split("/")
            for depth in range(1, len(parts) + 1):
                tracked_paths.add("/".join(parts[:depth]))

        required = set(REQUIRED_IN_CORE_ARCHIVE)
        # Anything the shell entrypoints resolve against the repository root has
        # to be in the archive too; derive it rather than maintaining a list.
        for script in ROOT.glob("scripts/**/*.sh"):
            text = script.read_text(encoding="utf-8")
            for reference in ROOT_REFERENCE.findall(text):
                if "$" in reference:
                    continue
                if reference in tracked_paths:
                    required.add(reference)
        # If the derivation ever stops matching anything, the loop above becomes
        # a no-op and this test silently degrades into re-checking the static
        # list; a narrowed filter has to fail here rather than pass quietly.
        self.assertGreater(
            len(required), len(REQUIRED_IN_CORE_ARCHIVE),
            "no repository-root reference was derived from the shell entrypoints",
        )

        for reference in sorted(required):
            with self.subTest(path=reference):
                self.assertIn(
                    reference, members,
                    f"{reference} is dereferenced at runtime but is not in the "
                    f"release core archive",
                )

    def test_release_generates_sbom_checksums_attestation_and_metadata(self) -> None:
        self.assertIn("format: spdx-json", self.text)
        self.assertIn("sha256sum", self.text)
        self.assertIn("actions/attest-build-provenance@", self.text)
        self.assertIn("RELEASE-METADATA.json", self.text)
        self.assertIn("gh release create", self.text)


class ReleaseSourceGuardTests(unittest.TestCase):
    """infra_require_clean_main_source must distinguish three states.

    Every Git probe fails in an unpacked release archive, and the guard used to
    read that as "detached HEAD" and block `make bootstrap` for anyone who
    installed from a release asset.  "Not a checkout" is a third answer, not a
    failed check -- and it has to stay distinguishable from "not an Infra tree
    at all", otherwise the guard degrades into always returning 0.
    """

    def run_guard(self, target: Path) -> subprocess.CompletedProcess[str]:
        guard = ROOT / "scripts" / "lib" / "host-workload-guard.sh"
        return subprocess.run(
            ["bash", "-c", f'set -u; . "{guard}"; infra_require_clean_main_source "{target}"'],
            capture_output=True, text=True,
        )

    def test_unpacked_release_archive_is_allowed_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("9.9.9\n")
            result = self.run_guard(root)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("unpacked archive", result.stderr)

    def test_a_directory_that_is_neither_is_still_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_guard(Path(tmp))
        self.assertEqual(1, result.returncode)
        self.assertIn("neither a Git checkout", result.stderr)

    def test_a_git_checkout_off_main_is_still_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "feature"], cwd=root, check=True)
            (root / "VERSION").write_text("9.9.9\n")
            result = self.run_guard(root)
        self.assertEqual(1, result.returncode)
        self.assertIn("source branch must be main", result.stderr)

    def test_an_archive_unpacked_inside_a_checkout_does_not_borrow_its_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enclosing = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=enclosing, check=True)
            nested = enclosing / "nested"
            nested.mkdir()
            (nested / "VERSION").write_text("9.9.9\n")
            result = self.run_guard(nested)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("unpacked archive", result.stderr)


if __name__ == "__main__":
    unittest.main()
