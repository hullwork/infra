from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "infra.py"
PROJECT = ROOT / "bootstrap" / "argocd-project.yaml"

SPEC = importlib.util.spec_from_file_location("infra_compiler", CLI)
assert SPEC and SPEC.loader
INFRA = importlib.util.module_from_spec(SPEC)
sys.dont_write_bytecode = True
SPEC.loader.exec_module(INFRA)

BASE_ARGS = (
    "--catalog", "catalog/packages",
    "--stack", "examples/stacks/demo.yaml",
    "--profile", "examples/profiles/local.yaml",
    "--lock", "versions.lock.yaml",
)


def render(*args: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(CLI), "render", *BASE_ARGS, *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return list(yaml.safe_load_all(result.stdout))


class SyncPolicyDefaultTests(unittest.TestCase):
    """Deletion is opt-in.

    The recorded accident this pins against: an automated sync with prune on
    removed a stateful workload and its object storage with it. `prune` had
    never been written down anywhere -- it was the implicit default of the
    renderer, so no review ever saw it.
    """

    def test_prune_is_off_when_a_profile_does_not_ask_for_it(self) -> None:
        policy = INFRA.sync_policy({"spec": {"syncPolicy": {"automated": True}}})
        self.assertEqual(False, policy["automated"]["prune"])
        # Written out rather than omitted: an absent key is invisible to review
        # and to `grep`, and Argo CD reads it as false anyway.
        self.assertIn("prune", policy["automated"])

    def test_prune_is_off_when_a_profile_has_no_sync_policy_at_all(self) -> None:
        policy = INFRA.sync_policy({"spec": {}})
        self.assertEqual(False, policy["automated"]["prune"])

    def test_prune_stays_available_as_an_explicit_opt_in(self) -> None:
        # Guards the other direction: a gate that returns false unconditionally
        # would pass the two tests above while breaking every profile that
        # legitimately asks for pruning.
        policy = INFRA.sync_policy({"spec": {"syncPolicy": {"prune": True}}})
        self.assertEqual(True, policy["automated"]["prune"])

    def test_self_heal_stays_on_and_server_side_apply_is_kept(self) -> None:
        policy = INFRA.sync_policy({"spec": {}})
        self.assertEqual(True, policy["automated"]["selfHeal"])
        self.assertIn("ServerSideApply=true", policy["syncOptions"])
        self.assertEqual(False, INFRA.sync_policy(
            {"spec": {"syncPolicy": {"selfHeal": False}}})["automated"]["selfHeal"])

    def test_rendered_applications_carry_the_safe_default(self) -> None:
        for document in render("--format", "applications"):
            with self.subTest(name=document["metadata"]["name"]):
                self.assertEqual(
                    False, document["spec"]["syncPolicy"]["automated"]["prune"]
                )
        for document in render("--format", "applicationset"):
            with self.subTest(name=document["metadata"]["name"]):
                self.assertEqual(
                    False,
                    document["spec"]["template"]["spec"]["syncPolicy"]["automated"]["prune"],
                )

    def test_shipped_profile_and_contract_agree_with_the_renderer(self) -> None:
        # Three places state this default. They drifted apart before; pin them
        # to each other so the next change has to move all three.
        profile = yaml.safe_load((ROOT / "examples" / "profiles" / "local.yaml").read_text(
            encoding="utf-8"))
        self.assertEqual(False, profile["spec"]["syncPolicy"]["prune"])
        schema = yaml.safe_load(
            (ROOT / "contracts" / "v1alpha1" / "cluster-profile.schema.json").read_text(
                encoding="utf-8")
        )
        self.assertEqual(
            False,
            schema["properties"]["spec"]["properties"]["syncPolicy"]["properties"]["prune"]["default"],
        )


class PortableProfileTests(unittest.TestCase):
    def _inputs(self):
        args = type("Args", (), {
            "catalog": [ROOT / "catalog/packages"],
            "stack": ROOT / "examples/stacks/demo.yaml",
            "profile": ROOT / "examples/profiles/local.yaml",
            "lock": ROOT / "versions.lock.yaml",
        })()
        return INFRA.load_and_validate(args)

    def test_management_namespace_is_configurable_in_both_formats(self):
        packages, stack, profile, lock = self._inputs()
        profile["spec"]["argocdNamespace"] = "delivery-system"
        INFRA.validate_profile(profile, Path("profile.yaml"))
        for renderer in (INFRA.render_applications, INFRA.render_application_sets):
            objects = renderer(packages, stack, profile, lock)
            self.assertTrue(objects)
            for obj in objects:
                self.assertEqual("delivery-system", obj["metadata"]["namespace"])
        profile["spec"]["argocdNamespace"] = "invalid/namespace"
        with self.assertRaises(INFRA.ContractError):
            INFRA.validate_profile(profile, Path("profile.yaml"))

    def test_deletion_controls_are_independent_of_pruning(self):
        packages, stack, profile, lock = self._inputs()
        for prune in (False, True):
            profile["spec"]["syncPolicy"]["prune"] = prune
            obj = INFRA.render_application_sets(packages, stack, profile, lock)[0]
            self.assertEqual({"applicationsSync": "create-update", "preserveResourcesOnDeletion": True},
                             obj["spec"]["syncPolicy"])
            self.assertEqual(prune, obj["spec"]["template"]["spec"]["syncPolicy"]["automated"]["prune"])
        profile["spec"]["applicationSetPolicy"] = {
            "applicationsSync": "sync", "preserveResourcesOnDeletion": False,
        }
        INFRA.validate_profile(profile, Path("profile.yaml"))
        obj = INFRA.render_application_sets(packages, stack, profile, lock)[0]
        self.assertEqual(profile["spec"]["applicationSetPolicy"], obj["spec"]["syncPolicy"])
        profile["spec"]["applicationSetPolicy"]["applicationsSync"] = "unknown"
        with self.assertRaises(INFRA.ContractError):
            INFRA.validate_profile(profile, Path("profile.yaml"))

    def test_local_controller_honors_per_set_policy(self):
        bootstrap = (ROOT / "scripts/bootstrap.sh").read_text()
        self.assertIn('"applicationsetcontroller.enable.policy.override":"true"', bootstrap)
        self.assertIn("rollout restart deployment/argocd-applicationset-controller", bootstrap)


class AppProjectGuardrailTests(unittest.TestCase):
    """The bootstrapped AppProject has to be narrower than Argo CD's built-in.

    A revision of this file once re-stated Argo CD's own wildcards on all four
    axes. It parsed, it was applied, and anyone asking "is there an AppProject
    restriction?" found it and stopped looking -- while nothing was restricted.
    """

    def setUp(self) -> None:
        self.project = yaml.safe_load(PROJECT.read_text(encoding="utf-8"))
        self.spec = self.project["spec"]

    def test_it_replaces_the_built_in_permissive_project(self) -> None:
        self.assertEqual("AppProject", self.project["kind"])
        # `ClusterProfile.spec.project` falls back to this name, so narrowing
        # any other project name would leave the fallback wide open.
        self.assertEqual("default", self.project["metadata"]["name"])
        self.assertEqual(
            "default",
            INFRA.load_yaml(ROOT / "examples" / "profiles" / "local.yaml")["spec"]["project"],
        )

    def test_source_repositories_are_enumerated(self) -> None:
        self.assertNotIn("*", self.spec["sourceRepos"])
        self.assertTrue(self.spec["sourceRepos"], "sourceRepos must not be empty")
        for repository in self.spec["sourceRepos"]:
            with self.subTest(repository=repository):
                # A bare "*" is not the only way to lose the axis: "oci://*" or
                # "https://*" widen it just as much.
                self.assertNotIn("*", repository)

    def test_destinations_enumerate_clusters(self) -> None:
        self.assertTrue(self.spec["destinations"])
        for destination in self.spec["destinations"]:
            with self.subTest(destination=destination):
                self.assertNotEqual("*", destination.get("server"))
                self.assertNotEqual("*", destination.get("name"))
                self.assertTrue(
                    destination.get("server") or destination.get("name"),
                    "a destination must pin a cluster by server or by name",
                )

    def test_cluster_scoped_kinds_are_enumerated(self) -> None:
        whitelist = self.spec["clusterResourceWhitelist"]
        self.assertGreater(len(whitelist), 1)
        for entry in whitelist:
            with self.subTest(entry=entry):
                self.assertNotEqual("*", entry["group"])
                self.assertNotEqual("*", entry["kind"])
        # The kinds the bootstrapped clusters genuinely need. Dropping one here
        # is a reviewed change, not a silent regression.
        kinds = {entry["kind"] for entry in whitelist}
        self.assertLessEqual(
            {"Namespace", "CustomResourceDefinition", "ClusterRole", "ClusterRoleBinding"},
            kinds,
        )

    def test_every_profile_cluster_has_a_permitted_destination(self) -> None:
        # The guardrail and the shipped profile must agree, otherwise the first
        # `make bootstrap` refuses the first `make render` and the fastest fix
        # anyone finds is to put the wildcards back.
        profile = INFRA.load_yaml(ROOT / "examples" / "profiles" / "local.yaml")
        permitted_servers = {
            destination.get("server") for destination in self.spec["destinations"]
        }
        permitted_names = {destination.get("name") for destination in self.spec["destinations"]}
        for cluster in profile["spec"]["clusters"]:
            with self.subTest(cluster=cluster["name"]):
                self.assertTrue(
                    cluster["server"] in permitted_servers
                    or cluster["name"] in permitted_names,
                    f"{cluster['name']} is rendered but not permitted by the AppProject",
                )

    def test_bootstrap_applies_the_project_before_it_declares_success(self) -> None:
        bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn(
            'kube_infra -n argocd apply -f "$infra_root/bootstrap/argocd-project.yaml"',
            bootstrap,
        )

    def test_the_widening_procedure_is_documented(self) -> None:
        # `grep -rn prune` over this repository used to return zero prose hits:
        # the behaviour existed only as a default buried in the renderer.
        configuration = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
        self.assertIn("prune", configuration)
        self.assertIn("sourceRepos", configuration)
        self.assertIn("ServerSideApply", configuration)


if __name__ == "__main__":
    unittest.main()
