#!/usr/bin/env python3
"""Validate infra contracts and render Argo CD desired state.

This is intentionally a build-time tool, not a controller. Git remains the source of
truth and Argo CD performs reconciliation after bootstrap.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by clean-environment users
    raise SystemExit("PyYAML is required; run: python3 -m pip install -r requirements-dev.txt") from exc

try:
    import jsonschema
except ImportError as exc:  # pragma: no cover - exercised by clean-environment users
    raise SystemExit("jsonschema is required; run: python3 -m pip install -r requirements-dev.txt") from exc


API_VERSION = "infra.convee.io/v1alpha1"
DNS_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
DNS_LABEL_MAX_LENGTH = 63
DNS_SUBDOMAIN_MAX_LENGTH = 253
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
EXACT_VERSION = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
CATEGORIES = {"ClusterProvider", "PlatformAddon", "WorkloadPackage"}
SOURCE_TYPES = {"oci", "helm", "git"}
SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "v1alpha1"
SCHEMAS = {
    "Package": SCHEMA_ROOT / "package.schema.json",
    "Stack": SCHEMA_ROOT / "stack.schema.json",
    "ClusterProfile": SCHEMA_ROOT / "cluster-profile.schema.json",
    "VersionLock": SCHEMA_ROOT / "version-lock.schema.json",
}


class ContractError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"{path}: cannot read YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError(f"{path}: expected one YAML object")
    return document


def load_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: cannot read JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError(f"{path}: expected one JSON object")
    return document


def require_contract(document: dict[str, Any], kind: str, path: Path) -> None:
    if document.get("apiVersion") != API_VERSION:
        raise ContractError(f"{path}: apiVersion must be {API_VERSION}")
    if document.get("kind") != kind:
        raise ContractError(f"{path}: kind must be {kind}")
    schema_path = SCHEMAS[kind]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(document),
        key=lambda error: ".".join(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "document"
        raise ContractError(f"{path}: schema violation at {location}: {error.message}")


def require_name(document: dict[str, Any], path: Path) -> str:
    metadata = document.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    if (
        not isinstance(name, str)
        or len(name) > DNS_LABEL_MAX_LENGTH
        or not DNS_NAME.fullmatch(name)
    ):
        raise ContractError(
            f"{path}: metadata.name must be a DNS-compatible label of at most "
            f"{DNS_LABEL_MAX_LENGTH} characters"
        )
    return name


def validate_package(document: dict[str, Any], path: Path) -> str:
    require_contract(document, "Package", path)
    name = require_name(document, path)
    spec = document.get("spec")
    if not isinstance(spec, dict):
        raise ContractError(f"{path}: spec must be an object")
    if spec.get("category") not in CATEGORIES:
        raise ContractError(f"{path}: spec.category must be one of {sorted(CATEGORIES)}")
    namespace = spec.get("namespace")
    if (
        not isinstance(namespace, str)
        or len(namespace) > DNS_LABEL_MAX_LENGTH
        or not DNS_NAME.fullmatch(namespace)
    ):
        raise ContractError(f"{path}: spec.namespace must be a DNS-compatible name")
    source = spec.get("source")
    if not isinstance(source, dict) or source.get("type") not in SOURCE_TYPES:
        raise ContractError(f"{path}: spec.source.type must be one of {sorted(SOURCE_TYPES)}")
    source_type = source["type"]
    repo_url = source.get("repoURL")
    if not isinstance(repo_url, str) or not repo_url:
        raise ContractError(f"{path}: spec.source.repoURL is required")
    if source_type == "oci":
        if not repo_url.startswith("oci://"):
            raise ContractError(f"{path}: OCI repoURL must start with oci://")
        renderer = source.get("renderer", "directory")
        if renderer not in {"directory", "helm"}:
            raise ContractError(f"{path}: OCI renderer must be directory or helm")
        if renderer == "directory" and spec.get("values"):
            raise ContractError(f"{path}: OCI directory packages cannot declare Helm values")
    elif source_type == "helm":
        if not isinstance(source.get("chart"), str) or not source["chart"]:
            raise ContractError(f"{path}: Helm source requires chart")
    elif not isinstance(source.get("path"), str) or not source["path"]:
        raise ContractError(f"{path}: Git source requires path")
    else:
        renderer = source.get("renderer", "directory")
        if renderer not in {"directory", "helm"}:
            raise ContractError(f"{path}: Git renderer must be directory or helm")
        if renderer == "directory" and spec.get("values"):
            raise ContractError(f"{path}: Git directory packages cannot declare Helm values")
    capabilities = spec.get("capabilities", {})
    if not isinstance(capabilities, dict):
        raise ContractError(f"{path}: spec.capabilities must be an object")
    for field in ("provides", "requires"):
        values = capabilities.get(field, [])
        if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
            raise ContractError(f"{path}: spec.capabilities.{field} must be a string array")
        if len(values) != len(set(values)):
            raise ContractError(f"{path}: spec.capabilities.{field} contains duplicates")
    return name


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_dir():
        raise ContractError(f"{path}: catalog directory does not exist")
    packages: dict[str, dict[str, Any]] = {}
    package_paths = sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
    if not package_paths:
        raise ContractError(f"{path}: catalog contains no packages")
    for package_path in package_paths:
        package = load_yaml(package_path)
        name = validate_package(package, package_path)
        if name in packages:
            raise ContractError(f"{package_path}: duplicate package {name}")
        packages[name] = package
    return packages


def load_catalogs(paths: list[Path]) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for path in paths:
        for name, package in load_catalog(path).items():
            if name in packages:
                raise ContractError(f"{path}: duplicate package {name}")
            packages[name] = package
    return packages


def validate_stack(document: dict[str, Any], path: Path, packages: dict[str, dict[str, Any]]) -> str:
    require_contract(document, "Stack", path)
    name = require_name(document, path)
    entries = document.get("spec", {}).get("packages")
    if not isinstance(entries, list) or not entries:
        raise ContractError(f"{path}: spec.packages must be a non-empty array")
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ContractError(f"{path}: spec.packages[{index}].name is required")
        package_name = entry["name"]
        if package_name not in packages:
            raise ContractError(f"{path}: package {package_name!r} is not in the catalog")
        if package_name in seen:
            raise ContractError(f"{path}: duplicate package {package_name!r}")
        seen.add(package_name)
        namespace = entry.get("namespace")
        if namespace is not None and (
            not isinstance(namespace, str)
            or len(namespace) > DNS_LABEL_MAX_LENGTH
            or not DNS_NAME.fullmatch(namespace)
        ):
            raise ContractError(f"{path}: package {package_name!r} has invalid namespace")
        if namespace is not None and packages[package_name]["spec"].get("namespaceMode") == "values":
            raise ContractError(
                f"{path}: multi-namespace package {package_name!r} cannot use namespace override; "
                "override its documented namespace values instead"
            )
        if "values" in entry and not isinstance(entry["values"], dict):
            raise ContractError(f"{path}: package {package_name!r} values must be an object")
        package_source = packages[package_name]["spec"]["source"]
        if (
            package_source["type"] in {"oci", "git"}
            and package_source.get("renderer", "directory") == "directory"
            and entry.get("values")
        ):
            raise ContractError(f"{path}: directory package {package_name!r} cannot receive Helm values")
        match_labels = entry.get("target", {}).get("matchLabels", {})
        if match_labels and not all(isinstance(key, str) and isinstance(value, str) for key, value in match_labels.items()):
            raise ContractError(f"{path}: package {package_name!r} target labels must be strings")
    return name


def validate_profile(document: dict[str, Any], path: Path) -> str:
    require_contract(document, "ClusterProfile", path)
    name = require_name(document, path)
    spec = document.get("spec", {})
    clusters = spec.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise ContractError(f"{path}: spec.clusters must be a non-empty array")
    seen: set[str] = set()
    for index, cluster in enumerate(clusters):
        if not isinstance(cluster, dict):
            raise ContractError(f"{path}: spec.clusters[{index}] must be an object")
        cluster_name = cluster.get("name")
        server = cluster.get("server")
        if (
            not isinstance(cluster_name, str)
            or len(cluster_name) > DNS_LABEL_MAX_LENGTH
            or not DNS_NAME.fullmatch(cluster_name)
        ):
            raise ContractError(f"{path}: cluster {index} has invalid name")
        if cluster_name in seen:
            raise ContractError(f"{path}: duplicate cluster {cluster_name!r}")
        seen.add(cluster_name)
        if not isinstance(server, str) or not server:
            raise ContractError(f"{path}: cluster {cluster_name!r} requires server")
        capabilities = cluster.get("capabilities", {})
        provided = capabilities.get("provides", []) if isinstance(capabilities, dict) else None
        if not isinstance(provided, list) or not all(isinstance(value, str) and value for value in provided):
            raise ContractError(f"{path}: cluster {cluster_name!r} capabilities.provides must be a string array")
        if len(provided) != len(set(provided)):
            raise ContractError(f"{path}: cluster {cluster_name!r} capabilities.provides contains duplicates")
    return name


def matching_clusters(entry: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    selector = entry.get("target", {}).get("matchLabels", {})
    return [
        cluster
        for cluster in profile["spec"]["clusters"]
        if all(cluster.get("labels", {}).get(key) == value for key, value in selector.items())
    ]


def validate_capabilities(
    packages: dict[str, dict[str, Any]], stack: dict[str, Any], profile: dict[str, Any]
) -> None:
    entries = stack["spec"]["packages"]
    for entry in entries:
        if not matching_clusters(entry, profile):
            raise ContractError(f"package {entry['name']!r} target selector matches no cluster")

    for cluster in profile["spec"]["clusters"]:
        cluster_entries = [entry for entry in entries if cluster in matching_clusters(entry, profile)]
        selected = [entry["name"] for entry in cluster_entries]
        cluster_provides = set(cluster.get("capabilities", {}).get("provides", []))
        providers: dict[str, list[str]] = {}
        for name in selected:
            for capability in packages[name]["spec"].get("capabilities", {}).get("provides", []):
                providers.setdefault(capability, []).append(name)

        graph: dict[str, set[str]] = {name: set() for name in selected}
        for name in selected:
            required = packages[name]["spec"].get("capabilities", {}).get("requires", [])
            for capability in required:
                if capability in cluster_provides:
                    continue
                matches = providers.get(capability, [])
                if not matches:
                    raise ContractError(
                        f"package {name!r} on cluster {cluster['name']!r} requires unavailable capability {capability!r}"
                    )
                if len(matches) > 1:
                    joined = ", ".join(sorted(matches))
                    raise ContractError(
                        f"package {name!r} on cluster {cluster['name']!r} capability {capability!r} "
                        f"has ambiguous providers: {joined}"
                    )
                graph[name].add(matches[0])

        stack_name = stack["metadata"]["name"]
        for name in selected:
            application_name = f"{cluster['name']}-{stack_name}-{name}"
            if len(application_name) > DNS_SUBDOMAIN_MAX_LENGTH:
                raise ContractError(
                    f"rendered Application name {application_name!r} exceeds "
                    f"{DNS_SUBDOMAIN_MAX_LENGTH} characters"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str, trail: list[str]) -> None:
            if name in visiting:
                cycle_start = trail.index(name)
                cycle = " -> ".join([*trail[cycle_start:], name])
                raise ContractError(f"package capability dependency cycle on cluster {cluster['name']!r}: {cycle}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in sorted(graph[name]):
                visit(dependency, [*trail, name])
            visiting.remove(name)
            visited.add(name)

        for package_name in sorted(graph):
            visit(package_name, [])


def validate_lock(
    document: dict[str, Any], path: Path, packages: dict[str, dict[str, Any]], stack: dict[str, Any]
) -> None:
    require_contract(document, "VersionLock", path)
    locks = document.get("spec", {}).get("packages")
    if not isinstance(locks, dict):
        raise ContractError(f"{path}: spec.packages must be an object")
    selected = {entry["name"] for entry in stack["spec"]["packages"]}
    for name in sorted(selected):
        lock = locks.get(name)
        if not isinstance(lock, dict):
            raise ContractError(f"{path}: selected package {name!r} has no version lock")
        source_type = packages[name]["spec"]["source"]["type"]
        if source_type == "oci":
            digest = lock.get("digest")
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise ContractError(f"{path}: OCI package {name!r} requires a sha256 digest")
            if "revision" in lock:
                raise ContractError(f"{path}: OCI package {name!r} cannot declare an unused Git revision")
        elif source_type == "git":
            revision = lock.get("revision")
            if not isinstance(revision, str) or not COMMIT.fullmatch(revision):
                raise ContractError(f"{path}: Git package {name!r} requires an exact 40-character commit")
            if "version" in lock:
                raise ContractError(f"{path}: Git package {name!r} cannot declare an unused semantic version")
            if "digest" in lock:
                raise ContractError(f"{path}: Git package {name!r} cannot declare an unused digest")
        else:
            version = lock.get("version")
            if not isinstance(version, str) or not EXACT_VERSION.fullmatch(version):
                raise ContractError(f"{path}: Helm package {name!r} requires an exact semantic version")
            if "digest" in lock or "revision" in lock:
                raise ContractError(f"{path}: Helm package {name!r} cannot declare an unused digest or revision")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def set_nested_value(values: dict[str, Any], path: str, value: dict[str, str]) -> None:
    parts = path.split(".")
    if not parts or any(not part for part in parts):
        raise ContractError(f"invalid release image valuePath {path!r}")
    current = values
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ContractError(f"release image valuePath {path!r} conflicts with another value")
        current = child
    current[parts[-1]] = value


def verify_release_artifacts(metadata_path: Path, metadata: dict[str, Any]) -> None:
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ContractError(f"{metadata_path}: artifacts are required")
    root = metadata_path.resolve().parent
    for name in ("chart", "packageDescriptor"):
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict):
            raise ContractError(f"{metadata_path}: artifacts.{name} is required")
        filename = artifact.get("filename")
        expected = artifact.get("sha256")
        if not isinstance(filename, str) or not isinstance(expected, str) or not DIGEST.fullmatch(expected):
            raise ContractError(f"{metadata_path}: artifacts.{name} requires filename and sha256")
        artifact_path = (root / filename).resolve()
        if artifact_path.parent != root:
            raise ContractError(f"{metadata_path}: artifact filename must not escape its directory")
        try:
            actual = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ContractError(f"{metadata_path}: cannot read artifact {filename}: {exc}") from exc
        if actual != expected:
            raise ContractError(f"{metadata_path}: checksum mismatch for artifact {filename}")


def import_release_metadata(
    metadata_path: Path, catalog_path: Path, lock_path: Path, output_path: Path
) -> str:
    metadata = load_json(metadata_path)
    if metadata.get("schemaVersion") != 1:
        raise ContractError(f"{metadata_path}: schemaVersion must be 1")
    verify_release_artifacts(metadata_path, metadata)
    name = metadata.get("name")
    version = metadata.get("version")
    package = metadata.get("package")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ContractError(f"{metadata_path}: name and version are required")
    packages = load_catalog(catalog_path)
    if name not in packages:
        raise ContractError(f"{metadata_path}: package {name!r} is not in the catalog")
    if not isinstance(package, dict) or package.get("type") != "helm-oci":
        raise ContractError(f"{metadata_path}: package.type must be helm-oci")
    digest = package.get("digest")
    url = package.get("url")
    immutable_ref = package.get("immutableRef")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ContractError(f"{metadata_path}: package.digest must be a sha256 digest")
    expected_url = packages[name]["spec"]["source"]["repoURL"]
    if url != expected_url or immutable_ref != f"{url}@{digest}":
        raise ContractError(f"{metadata_path}: package URL or immutableRef does not match the catalog")

    locked_values: dict[str, Any] = {}
    runtime = metadata.get("runtimeImages", {})
    if not isinstance(runtime, dict):
        raise ContractError(f"{metadata_path}: runtimeImages must be an object")
    images = runtime.get("images", [])
    if not isinstance(images, list):
        raise ContractError(f"{metadata_path}: runtimeImages.images must be an array")
    for image in images:
        if not isinstance(image, dict):
            raise ContractError(f"{metadata_path}: every runtime image must be an object")
        repository = image.get("repository")
        image_digest = image.get("digest")
        value_path = image.get("valuePath")
        if (
            not isinstance(repository, str)
            or not repository
            or not isinstance(image_digest, str)
            or not DIGEST.fullmatch(image_digest)
            or not isinstance(value_path, str)
        ):
            raise ContractError(
                f"{metadata_path}: runtime image requires repository, sha256 digest, and valuePath"
            )
        if image.get("immutableRef") != f"{repository}@{image_digest}":
            raise ContractError(f"{metadata_path}: runtime image immutableRef is inconsistent")
        set_nested_value(locked_values, value_path, {"repository": repository, "digest": image_digest})

    lock = load_yaml(lock_path)
    require_contract(lock, "VersionLock", lock_path)
    lock_entry: dict[str, Any] = {"version": version, "digest": digest}
    if locked_values:
        lock_entry["values"] = locked_values
    lock.setdefault("spec", {}).setdefault("packages", {})[name] = lock_entry
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    return name


def argo_source(package: dict[str, Any], lock: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    source = package["spec"]["source"]
    source_type = source["type"]
    if source_type == "oci":
        rendered = {
            "repoURL": source["repoURL"],
            "targetRevision": lock["digest"],
            "path": source.get("path", "."),
        }
        if source.get("renderer", "directory") == "helm" and values:
            rendered["helm"] = {"valuesObject": values}
        return rendered
    if source_type == "git":
        rendered = {
            "repoURL": source["repoURL"],
            "targetRevision": lock["revision"],
            "path": source["path"],
        }
        if source.get("renderer", "directory") == "helm":
            rendered["helm"] = {"valuesObject": values}
        return rendered
    rendered = {
        "repoURL": source["repoURL"],
        "chart": source["chart"],
        "targetRevision": lock["version"],
    }
    if values:
        rendered["helm"] = {"valuesObject": values}
    return rendered


def lock_annotations(lock: dict[str, Any]) -> dict[str, str]:
    annotations = {}
    for field in ("version", "digest", "revision"):
        if field in lock:
            annotations[f"infra.convee.io/{field}"] = lock[field]
    return annotations


def sync_policy(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("spec", {}).get("syncPolicy", {})
    policy: dict[str, Any] = {
        "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"]
    }
    if configured.get("automated", True):
        policy["automated"] = {
            # Deleting live objects is the one reconciliation outcome no
            # rollback undoes, so it is opt-in.  ServerSideApply above makes it
            # worse than it looks: fields Argo CD does not own are excluded from
            # the diff, so drift stops being reported while pruning keeps
            # deleting.  Automated create/update stays on by default; automated
            # deletion has to be a written decision in the ClusterProfile.
            "prune": configured.get("prune", False),
            # Self-heal only rewrites the objects the desired state declares; it
            # never removes an object that has left the stack.  That is
            # recoverable, so it stays on to keep reconciliation converging.
            "selfHeal": configured.get("selfHeal", True),
        }
    return policy


def render_application_sets(
    packages: dict[str, dict[str, Any]], stack: dict[str, Any], profile: dict[str, Any], lock: dict[str, Any]
) -> list[dict[str, Any]]:
    output = []
    stack_name = stack["metadata"]["name"]
    project = profile["spec"].get("project", "default")
    for entry in stack["spec"]["packages"]:
        name = entry["name"]
        package = packages[name]
        locked = lock["spec"]["packages"][name]
        namespace = entry.get("namespace", package["spec"]["namespace"])
        values = deep_merge(package["spec"].get("values", {}), entry.get("values", {}))
        values = deep_merge(values, locked.get("values", {}))
        elements = [
            {"cluster": cluster["name"], "server": cluster["server"]}
            for cluster in matching_clusters(entry, profile)
        ]
        output.append(
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "ApplicationSet",
                "metadata": {"name": f"{stack_name}-{name}", "namespace": "argocd"},
                "spec": {
                    "generators": [{"list": {"elements": elements}}],
                    "template": {
                        "metadata": {
                            "name": f"{{{{cluster}}}}-{stack_name}-{name}",
                            "labels": {
                                "infra.convee.io/stack": stack_name,
                                "infra.convee.io/package": name,
                                "infra.convee.io/category": package["spec"]["category"].lower(),
                            },
                            "annotations": lock_annotations(locked),
                        },
                        "spec": {
                            "project": project,
                            "source": argo_source(package, locked, values),
                            "destination": {"server": "{{server}}", "namespace": namespace},
                            "syncPolicy": sync_policy(profile),
                        },
                    },
                },
            }
        )
    return output


def render_applications(
    packages: dict[str, dict[str, Any]], stack: dict[str, Any], profile: dict[str, Any], lock: dict[str, Any]
) -> list[dict[str, Any]]:
    output = []
    stack_name = stack["metadata"]["name"]
    project = profile["spec"].get("project", "default")
    for entry in stack["spec"]["packages"]:
        name = entry["name"]
        package = packages[name]
        locked = lock["spec"]["packages"][name]
        namespace = entry.get("namespace", package["spec"]["namespace"])
        values = deep_merge(package["spec"].get("values", {}), entry.get("values", {}))
        values = deep_merge(values, locked.get("values", {}))
        for cluster in matching_clusters(entry, profile):
            output.append(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {
                        "name": f"{cluster['name']}-{stack_name}-{name}",
                        "namespace": "argocd",
                        "labels": {
                            "infra.convee.io/stack": stack_name,
                            "infra.convee.io/package": name,
                            "infra.convee.io/category": package["spec"]["category"].lower(),
                        },
                        "annotations": lock_annotations(locked),
                    },
                    "spec": {
                        "project": project,
                        "source": argo_source(package, locked, values),
                        "destination": {"server": cluster["server"], "namespace": namespace},
                        "syncPolicy": sync_policy(profile),
                    },
                }
            )
    return output


def load_and_validate(args: argparse.Namespace) -> tuple[dict[str, Any], ...]:
    packages = load_catalogs(args.catalog)
    stack = load_yaml(args.stack)
    validate_stack(stack, args.stack, packages)
    profile = load_yaml(args.profile)
    validate_profile(profile, args.profile)
    validate_capabilities(packages, stack, profile)
    lock = load_yaml(args.lock)
    validate_lock(lock, args.lock, packages, stack)
    return packages, stack, profile, lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "render"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--catalog", type=Path, action="append", required=True)
        subparser.add_argument("--stack", type=Path, required=True)
        subparser.add_argument("--profile", type=Path, required=True)
        subparser.add_argument("--lock", type=Path, required=True)
        if command == "render":
            subparser.add_argument("--format", choices=("applicationset", "applications"), default="applicationset")
            subparser.add_argument("--output", type=Path)
    importer = commands.add_parser("import-release")
    importer.add_argument("--metadata", type=Path, required=True)
    importer.add_argument("--catalog", type=Path, required=True)
    importer.add_argument("--lock", type=Path, required=True)
    importer.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "import-release":
            name = import_release_metadata(args.metadata, args.catalog, args.lock, args.output)
            print(f"imported immutable release for {name}")
            return 0
        packages, stack, profile, lock = load_and_validate(args)
        if args.command == "validate":
            selected = len(stack["spec"]["packages"])
            clusters = len(profile["spec"]["clusters"])
            print(f"validated {selected} package(s) for {clusters} cluster(s)")
            return 0
        if args.format == "applicationset":
            rendered = render_application_sets(packages, stack, profile, lock)
        else:
            rendered = render_applications(packages, stack, profile, lock)
        output = yaml.safe_dump_all(rendered, sort_keys=False, explicit_start=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
