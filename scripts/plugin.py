#!/usr/bin/env python3
"""Discover and validate infra integration plugins."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def validate_plugin(document: object, descriptor: Path) -> dict:
    if not isinstance(document, dict):
        raise ValueError(f"{descriptor}: expected one YAML object")
    if set(document) != {"apiVersion", "kind", "metadata", "spec"}:
        raise ValueError(f"{descriptor}: unknown or missing top-level field")
    if document["apiVersion"] != "infra.convee.io/v1alpha1" or document["kind"] != "IntegrationPlugin":
        raise ValueError(f"{descriptor}: unsupported apiVersion or kind")
    metadata = document["metadata"]
    spec = document["spec"]
    if not isinstance(metadata, dict) or set(metadata) != {"name"}:
        raise ValueError(f"{descriptor}: metadata must contain only name")
    name = metadata["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", name):
        raise ValueError(f"{descriptor}: invalid plugin name")
    allowed = {"entrypoint", "sourceEnv", "sourceRevision", "actions", "requires"}
    if not isinstance(spec, dict) or not {"entrypoint", "actions"} <= set(spec) or not set(spec) <= allowed:
        raise ValueError(f"{descriptor}: unknown or missing spec field")
    entrypoint = spec["entrypoint"]
    if not isinstance(entrypoint, str) or not re.fullmatch(r"\./[A-Za-z0-9._/-]+", entrypoint):
        raise ValueError(f"{descriptor}: invalid entrypoint")
    actions = spec["actions"]
    allowed_actions = {"doctor", "up", "status", "verify"}
    if not isinstance(actions, list) or not actions or len(actions) != len(set(actions)) or not set(actions) <= allowed_actions:
        raise ValueError(f"{descriptor}: invalid actions")
    source_env = spec.get("sourceEnv")
    if source_env is not None and (not isinstance(source_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", source_env)):
        raise ValueError(f"{descriptor}: invalid sourceEnv")
    revision = spec.get("sourceRevision")
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError(f"{descriptor}: invalid sourceRevision")
    requires = spec.get("requires", [])
    if not isinstance(requires, list) or len(requires) != len(set(requires)) or not all(isinstance(item, str) for item in requires):
        raise ValueError(f"{descriptor}: invalid requires")
    return document


def load_plugins(root: Path) -> dict[str, tuple[dict, Path]]:
    result = {}
    for descriptor in sorted(root.glob("*/plugin.yaml")):
        document = validate_plugin(yaml.safe_load(descriptor.read_text(encoding="utf-8")), descriptor)
        name = document["metadata"]["name"]
        if name in result:
            raise ValueError(f"duplicate plugin {name}")
        entrypoint = (descriptor.parent / document["spec"]["entrypoint"]).resolve()
        if entrypoint.parent != descriptor.parent.resolve() or not entrypoint.is_file():
            raise ValueError(f"{descriptor}: entrypoint must resolve inside its plugin directory")
        result[name] = (document, entrypoint)
    if not result:
        raise ValueError(f"no plugins found under {root}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "entrypoint", "source", "revision"))
    parser.add_argument("--plugins", type=Path, default=ROOT / "plugins")
    parser.add_argument("--name")
    parser.add_argument("--action", choices=("doctor", "up", "status", "verify"))
    args = parser.parse_args()
    try:
        plugins = load_plugins(args.plugins)
        if args.command == "list":
            print("\n".join(plugins))
            return 0
        if not args.name or args.name not in plugins:
            raise ValueError(f"unknown plugin: {args.name or '(missing)'}")
        document, entrypoint = plugins[args.name]
        if args.action and args.action not in document["spec"]["actions"]:
            raise ValueError(f"plugin {args.name} does not support action {args.action}")
        if args.command == "entrypoint":
            print(entrypoint)
        elif args.command == "revision":
            revision = document["spec"].get("sourceRevision")
            if not revision:
                raise ValueError(f"plugin {args.name} has no pinned sourceRevision")
            print(revision)
        else:
            env_name = document["spec"]["sourceEnv"]
            source = os.environ.get(env_name, "")
            if not source:
                raise ValueError(f"plugin {args.name} requires {env_name}")
            print(Path(source).resolve())
        return 0
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
