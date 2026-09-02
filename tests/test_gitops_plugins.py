from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "infra.py"
BASE_ARGS = [
    "--catalog", str(ROOT / "catalog" / "packages"),
    "--stack", str(ROOT / "examples" / "stacks" / "demo.yaml"),
    "--profile", str(ROOT / "examples" / "profiles" / "local.yaml"),
    "--lock", str(ROOT / "versions.lock.yaml"),
]


def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        check=check,
        text=True,
        capture_output=True,
        cwd=ROOT,
    )


class GitOpsPluginTests(unittest.TestCase):
    def test_contract_schemas_are_valid_json(self) -> None:
        schemas = sorted((ROOT / "contracts" / "v1alpha1").glob("*.schema.json"))
        self.assertEqual(6, len(schemas))
        for schema in schemas:
            with self.subTest(schema=schema.name):
                self.assertIsInstance(json.loads(schema.read_text()), dict)

    def test_example_validates(self) -> None:
        result = run_cli("validate", *BASE_ARGS)
        self.assertEqual("validated 1 package(s) for 1 cluster(s)\n", result.stdout)

    def test_real_third_party_helm_package_validates_and_renders(self) -> None:
        root = ROOT / "examples" / "third-party"
        args = [
            "--catalog", str(root / "catalog"),
            "--stack", str(root / "stack.yaml"),
            "--profile", str(root / "profile.yaml"),
            "--lock", str(root / "versions.lock.yaml"),
        ]
        result = run_cli("validate", *args)
        self.assertEqual("validated 1 package(s) for 1 cluster(s)\n", result.stdout)
        rendered = list(
            yaml.safe_load_all(
                run_cli("render", *args, "--format", "applications").stdout
            )
        )
        source = rendered[0]["spec"]["source"]
        self.assertEqual("https://charts.jetstack.io", source["repoURL"])
        self.assertEqual("cert-manager", source["chart"])
        self.assertEqual("v1.21.1", source["targetRevision"])
        self.assertTrue(source["helm"]["valuesObject"]["crds"]["enabled"])

    def test_applicationset_uses_oci_digest_as_revision(self) -> None:
        result = run_cli("render", *BASE_ARGS, "--format", "applicationset")
        documents = list(yaml.safe_load_all(result.stdout))
        self.assertEqual(1, len(documents))
        application_set = documents[0]
        self.assertEqual("ApplicationSet", application_set["kind"])
        source = application_set["spec"]["template"]["spec"]["source"]
        self.assertEqual(
            "sha256:" + "1" * 64,
            source["targetRevision"],
        )
        self.assertEqual("oci://ghcr.io/example/infra-packages/demo-web", source["repoURL"])
        self.assertNotIn("chart", source)
        self.assertEqual({"replicaCount": 2}, source["helm"]["valuesObject"])

    def test_compose_git_helm_packages_from_external_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            for catalog, name in ((first, "package-a"), (second, "package-b")):
                package = {
                    "apiVersion": "infra.convee.io/v1alpha1",
                    "kind": "Package",
                    "metadata": {"name": name},
                    "spec": {
                        "category": "WorkloadPackage",
                        "namespace": name,
                        "source": {
                            "type": "git",
                            "repoURL": f"https://example.invalid/{name}.git",
                            "path": "charts/app",
                            "renderer": "helm",
                        },
                    },
                }
                (catalog / f"{name}.yaml").write_text(yaml.safe_dump(package, sort_keys=False))
            stack = root / "stack.yaml"
            stack.write_text(
                "apiVersion: infra.convee.io/v1alpha1\nkind: Stack\nmetadata:\n  name: composed\n"
                "spec:\n  packages:\n    - name: package-a\n    - name: package-b\n"
            )
            lock = root / "lock.yaml"
            lock.write_text(
                "apiVersion: infra.convee.io/v1alpha1\nkind: VersionLock\nspec:\n  packages:\n"
                + "".join(
                    f"    {name}:\n      revision: {digit * 40}\n"
                    for name, digit in (("package-a", "a"), ("package-b", "b"))
                )
            )
            result = run_cli(
                "render",
                "--catalog", str(first),
                "--catalog", str(second),
                "--stack", str(stack),
                "--profile", str(ROOT / "examples" / "profiles" / "local.yaml"),
                "--lock", str(lock),
                "--format", "applications",
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
        documents = list(yaml.safe_load_all(result.stdout))
        self.assertEqual(
            ["https://example.invalid/package-a.git", "https://example.invalid/package-b.git"],
            [document["spec"]["source"]["repoURL"] for document in documents],
        )
        for document in documents:
            source = document["spec"]["source"]
            self.assertEqual(40, len(source["targetRevision"]))
            self.assertIn("valuesObject", source["helm"])

    def test_duplicate_external_catalog_package_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            for catalog in (first, second):
                (catalog / "duplicate.yaml").write_text(
                    "apiVersion: infra.convee.io/v1alpha1\n"
                    "kind: Package\n"
                    "metadata: {name: duplicate}\n"
                    "spec:\n"
                    "  category: WorkloadPackage\n"
                    "  namespace: duplicate\n"
                    "  source: {type: git, repoURL: https://example.invalid/duplicate.git, path: chart}\n"
                )
            stack = root / "stack.yaml"
            stack.write_text(
                "apiVersion: infra.convee.io/v1alpha1\n"
                "kind: Stack\n"
                "metadata: {name: duplicate-test}\n"
                "spec: {packages: [{name: duplicate}]}\n"
            )
            lock = root / "lock.yaml"
            lock.write_text(
                "apiVersion: infra.convee.io/v1alpha1\n"
                "kind: VersionLock\n"
                "spec: {packages: {duplicate: {revision: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}}}\n"
            )
            result = run_cli(
                "validate",
                "--catalog", str(first),
                "--catalog", str(second),
                "--stack", str(stack),
                "--profile", str(ROOT / "examples" / "profiles" / "local.yaml"),
                "--lock", str(lock),
                check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("duplicate package duplicate", result.stderr)

    def test_git_directory_package_cannot_receive_helm_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog"
            catalog.mkdir()
            (catalog / "directory.yaml").write_text(
                "apiVersion: infra.convee.io/v1alpha1\n"
                "kind: Package\n"
                "metadata: {name: directory}\n"
                "spec:\n"
                "  category: WorkloadPackage\n"
                "  namespace: directory\n"
                "  source: {type: git, repoURL: https://example.invalid/directory.git, path: manifest}\n"
            )
            stack = root / "stack.yaml"
            stack.write_text(
                "apiVersion: infra.convee.io/v1alpha1\n"
                "kind: Stack\n"
                "metadata: {name: directory-test}\n"
                "spec: {packages: [{name: directory, values: {replicaCount: 2}}]}\n"
            )
            lock = root / "lock.yaml"
            lock.write_text(
                "apiVersion: infra.convee.io/v1alpha1\n"
                "kind: VersionLock\n"
                "spec: {packages: {directory: {revision: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}}}\n"
            )
            result = run_cli(
                "validate",
                "--catalog", str(catalog),
                "--stack", str(stack),
                "--profile", str(ROOT / "examples" / "profiles" / "local.yaml"),
                "--lock", str(lock),
                check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("directory package 'directory' cannot receive Helm values", result.stderr)

    def test_plain_application_render_is_available_for_review(self) -> None:
        result = run_cli("render", *BASE_ARGS, "--format", "applications")
        documents = list(yaml.safe_load_all(result.stdout))
        self.assertEqual(["Application"], [document["kind"] for document in documents])
        self.assertEqual(
            "https://kubernetes.default.svc",
            documents[0]["spec"]["destination"]["server"],
        )
        self.assertIn(
            "ServerSideApply=true",
            documents[0]["spec"]["syncPolicy"]["syncOptions"],
        )

    def test_missing_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "versions.lock.yaml"
            lock.write_text(
                "apiVersion: infra.convee.io/v1alpha1\nkind: VersionLock\nspec:\n  packages: {}\n"
            )
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "versions.lock.yaml"))] = str(lock)
            result = run_cli("render", *args, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("has no version lock", result.stderr)

    def test_unused_lock_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = yaml.safe_load((ROOT / "versions.lock.yaml").read_text())
            lock["spec"]["packages"]["demo-web"]["revision"] = "a" * 40
            lock_path = Path(temporary) / "versions.lock.yaml"
            lock_path.write_text(yaml.safe_dump(lock, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "versions.lock.yaml"))] = str(lock_path)
            result = run_cli("validate", *args, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("cannot declare an unused Git revision", result.stderr)

    def test_unavailable_capability_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Path(temporary) / "catalog"
            shutil.copytree(ROOT / "catalog" / "packages", catalog)
            package_path = catalog / "demo-web.yaml"
            package = yaml.safe_load(package_path.read_text())
            package["spec"]["capabilities"]["requires"] = ["database.sql"]
            package_path.write_text(yaml.safe_dump(package, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "catalog" / "packages"))] = str(catalog)
            result = run_cli("validate", *args, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("requires unavailable capability 'database.sql'", result.stderr)

    def test_capability_dependency_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog"
            catalog.mkdir()
            for name, provides, requires in (
                ("package-a", "capability.a", "capability.b"),
                ("package-b", "capability.b", "capability.a"),
            ):
                package = {
                    "apiVersion": "infra.convee.io/v1alpha1",
                    "kind": "Package",
                    "metadata": {"name": name},
                    "spec": {
                        "category": "PlatformAddon",
                        "namespace": name,
                        "source": {
                            "type": "oci",
                            "repoURL": f"oci://registry.example/{name}",
                            "path": ".",
                        },
                        "capabilities": {"provides": [provides], "requires": [requires]},
                    },
                }
                (catalog / f"{name}.yaml").write_text(yaml.safe_dump(package, sort_keys=False))
            stack = root / "stack.yaml"
            stack.write_text(
                "apiVersion: infra.convee.io/v1alpha1\nkind: Stack\nmetadata:\n  name: cycle\n"
                "spec:\n  packages:\n    - name: package-a\n    - name: package-b\n"
            )
            lock = root / "lock.yaml"
            lock.write_text(
                "apiVersion: infra.convee.io/v1alpha1\nkind: VersionLock\nspec:\n  packages:\n"
                + "".join(
                    f"    {name}:\n      version: 1.0.0\n      digest: sha256:{digit * 64}\n"
                    for name, digit in (("package-a", "a"), ("package-b", "b"))
                )
            )
            result = run_cli(
                "validate",
                "--catalog", str(catalog),
                "--stack", str(stack),
                "--profile", str(ROOT / "examples" / "profiles" / "local.yaml"),
                "--lock", str(lock),
                check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("dependency cycle", result.stderr)

    def test_plugin_core_has_no_product_specific_names(self) -> None:
        roots = ["catalog", "contracts", "examples", "scripts/infra.py", "versions.lock.yaml"]
        # The portable core must not bind a first-party source repository.
        forbidden = ("github.com/convee/", "SOURCE_DIR")
        for relative in roots:
            path = ROOT / relative
            files = [path] if path.is_file() else [
                candidate for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix in {".json", ".py", ".yaml", ".yml"}
            ]
            for file_path in files:
                text = file_path.read_text(encoding="utf-8")
                with self.subTest(path=file_path):
                    self.assertFalse(any(name in text for name in forbidden))

    def test_stack_selector_limits_target_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stack = yaml.safe_load((ROOT / "examples" / "stacks" / "demo.yaml").read_text())
            stack["spec"]["packages"][0]["target"] = {"matchLabels": {"role": "workload"}}
            stack_path = root / "stack.yaml"
            stack_path.write_text(yaml.safe_dump(stack, sort_keys=False))
            profile = yaml.safe_load((ROOT / "examples" / "profiles" / "local.yaml").read_text())
            profile["spec"]["clusters"] = [
                {
                    "name": "management",
                    "server": "https://management.example",
                    "labels": {"role": "management"},
                    "capabilities": {"provides": []},
                },
                {
                    "name": "workload",
                    "server": "https://workload.example",
                    "labels": {"role": "workload"},
                    "capabilities": {"provides": []},
                },
            ]
            profile_path = root / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "examples" / "stacks" / "demo.yaml"))] = str(stack_path)
            args[args.index(str(ROOT / "examples" / "profiles" / "local.yaml"))] = str(profile_path)
            result = run_cli("render", *args, "--format", "applications")
        documents = list(yaml.safe_load_all(result.stdout))
        self.assertEqual(["https://workload.example"], [item["spec"]["destination"]["server"] for item in documents])

    def test_json_schema_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stack = yaml.safe_load((ROOT / "examples" / "stacks" / "demo.yaml").read_text())
            stack["spec"]["unexpected"] = True
            stack_path = Path(temporary) / "stack.yaml"
            stack_path.write_text(yaml.safe_dump(stack, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "examples" / "stacks" / "demo.yaml"))] = str(stack_path)
            result = run_cli("validate", *args, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("schema violation", result.stderr)

    def test_kubernetes_label_names_are_limited_to_63_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Path(temporary) / "catalog"
            shutil.copytree(ROOT / "catalog" / "packages", catalog)
            package_path = catalog / "demo-web.yaml"
            package = yaml.safe_load(package_path.read_text())
            package["metadata"]["name"] = "a" * 64
            package_path.write_text(yaml.safe_dump(package, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "catalog" / "packages"))] = str(catalog)
            result = run_cli("validate", *args, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("is too long", result.stderr)

    def test_release_import_locks_chart_and_runtime_image_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata_path = root / "package-metadata.json"
            output_lock = root / "versions.lock.yaml"
            chart_path = root / "demo-web-1.2.3.tgz"
            descriptor_path = root / "package.yaml"
            chart_path.write_bytes(b"chart")
            descriptor_path.write_bytes(b"descriptor")
            digest = "sha256:" + "d" * 64
            image_digest = "sha256:" + "e" * 64
            metadata_path.write_text(json.dumps({
                "schemaVersion": 1,
                "name": "demo-web",
                "version": "1.2.3",
                "package": {
                    "type": "helm-oci",
                    "url": "oci://ghcr.io/example/infra-packages/demo-web",
                    "digest": digest,
                    "immutableRef": "oci://ghcr.io/example/infra-packages/demo-web@" + digest,
                },
                "artifacts": {
                    "chart": {
                        "filename": chart_path.name,
                        "sha256": "sha256:" + hashlib.sha256(chart_path.read_bytes()).hexdigest(),
                    },
                    "packageDescriptor": {
                        "filename": descriptor_path.name,
                        "sha256": "sha256:" + hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
                    },
                },
                "runtimeImages": {
                    "publishedByThisWorkflow": True,
                    "images": [{
                        "component": "web",
                        "valuePath": "images.web",
                        "repository": "ghcr.io/example/demo-web",
                        "digest": image_digest,
                        "immutableRef": "ghcr.io/example/demo-web@" + image_digest,
                    }],
                },
            }))
            result = run_cli(
                "import-release",
                "--metadata", str(metadata_path),
                "--catalog", str(ROOT / "catalog" / "packages"),
                "--lock", str(ROOT / "versions.lock.yaml"),
                "--output", str(output_lock),
            )
            lock = yaml.safe_load(output_lock.read_text())
        self.assertEqual("imported immutable release for demo-web\n", result.stdout)
        entry = lock["spec"]["packages"]["demo-web"]
        self.assertEqual(digest, entry["digest"])
        self.assertEqual(
            {"repository": "ghcr.io/example/demo-web", "digest": image_digest},
            entry["values"]["images"]["web"],
        )

    def test_release_import_rejects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "chart.tgz").write_bytes(b"tampered")
            (root / "package.yaml").write_bytes(b"descriptor")
            metadata = {
                "schemaVersion": 1,
                "name": "demo-web",
                "version": "1.2.3",
                "artifacts": {
                    "chart": {"filename": "chart.tgz", "sha256": "sha256:" + "0" * 64},
                    "packageDescriptor": {
                        "filename": "package.yaml",
                        "sha256": "sha256:" + hashlib.sha256(b"descriptor").hexdigest(),
                    },
                },
            }
            metadata_path = root / "package-metadata.json"
            metadata_path.write_text(json.dumps(metadata))
            result = run_cli(
                "import-release",
                "--metadata", str(metadata_path),
                "--catalog", str(ROOT / "catalog" / "packages"),
                "--lock", str(ROOT / "versions.lock.yaml"),
                "--output", str(root / "lock.yaml"),
                check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("checksum mismatch", result.stderr)

    def test_lock_image_values_override_mutable_stack_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stack = yaml.safe_load((ROOT / "examples" / "stacks" / "demo.yaml").read_text())
            stack["spec"]["packages"][0]["values"] = {
                "images": {"web": {"repository": "example/web", "tag": "latest"}}
            }
            stack_path = root / "stack.yaml"
            stack_path.write_text(yaml.safe_dump(stack, sort_keys=False))
            lock = yaml.safe_load((ROOT / "versions.lock.yaml").read_text())
            digest = "sha256:" + "f" * 64
            lock["spec"]["packages"]["demo-web"]["values"] = {
                "images": {"web": {"repository": "example/web", "digest": digest}}
            }
            lock_path = root / "lock.yaml"
            lock_path.write_text(yaml.safe_dump(lock, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "examples" / "stacks" / "demo.yaml"))] = str(stack_path)
            args[args.index(str(ROOT / "versions.lock.yaml"))] = str(lock_path)
            result = run_cli("render", *args)
            rendered = list(yaml.safe_load_all(result.stdout))
        image = rendered[0]["spec"]["template"]["spec"]["source"]["helm"]["valuesObject"]["images"]["web"]
        self.assertEqual(digest, image["digest"])

    def test_multi_namespace_package_rejects_destination_namespace_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog"
            shutil.copytree(ROOT / "catalog" / "packages", catalog)
            package_path = catalog / "demo-web.yaml"
            package = yaml.safe_load(package_path.read_text())
            package["spec"]["namespaceMode"] = "values"
            package_path.write_text(yaml.safe_dump(package, sort_keys=False))
            stack = yaml.safe_load((ROOT / "examples" / "stacks" / "demo.yaml").read_text())
            stack["spec"]["packages"][0]["namespace"] = "misleading"
            stack_path = root / "stack.yaml"
            stack_path.write_text(yaml.safe_dump(stack, sort_keys=False))
            args = [*BASE_ARGS]
            args[args.index(str(ROOT / "catalog" / "packages"))] = str(catalog)
            args[args.index(str(ROOT / "examples" / "stacks" / "demo.yaml"))] = str(stack_path)
            result = run_cli("validate", *args, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("multi-namespace package", result.stderr)


if __name__ == "__main__":
    unittest.main()
