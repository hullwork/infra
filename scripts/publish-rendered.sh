#!/usr/bin/env bash
# Publish compiler output to the local GitOps repository, then register that
# exact Argo CD control-plane resource. This is generic render delivery; it
# never builds an application or interprets application-specific values.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"

name="${1:?usage: publish-rendered.sh NAME RENDERED_YAML}"
manifest="${2:?usage: publish-rendered.sh NAME RENDERED_YAML}"

[[ "$name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || {
  echo "invalid rendered stack name: $name" >&2
  exit 2
}
[ -s "$manifest" ] || {
  echo "missing rendered manifest: $manifest" >&2
  exit 2
}

infra_require_clean_main_source "$infra_root"
require_bins git kubectl
kube_infra -n gitops-system rollout status deployment/git-server --timeout=180s

deadline=$((SECONDS + 60))
until git ls-remote "$git_host_url" >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || {
    echo "local GitOps repository is unreachable: $git_host_url" >&2
    exit 1
  }
  sleep 2
done

python3 - "$manifest" <<'PY'
import sys
from pathlib import Path

import yaml

allowed = {"Application", "ApplicationSet"}
documents = list(yaml.safe_load_all(Path(sys.argv[1]).read_text(encoding="utf-8")))
if not documents or any(document.get("kind") not in allowed for document in documents):
    raise SystemExit("rendered input must contain only Argo Application/ApplicationSet resources")
PY

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/infra-rendered.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
checkout="$work_dir/repo"
path="rendered/$name"
git clone --quiet "$git_host_url" "$checkout"
mkdir -p "$checkout/$path"
cp "$manifest" "$checkout/$path/all.yaml"
git -C "$checkout" config user.name "Infra GitOps"
git -C "$checkout" config user.email "infra-gitops@localhost"
git -C "$checkout" add "$path"
if ! git -C "$checkout" diff --cached --quiet; then
  git -C "$checkout" commit --quiet -m "chore(gitops): publish rendered $name"
  git -C "$checkout" push --quiet origin main
else
  echo "GitOps repository already contains rendered/$name"
fi

kube_infra apply -f "$manifest"
echo "published and registered rendered/$name"
