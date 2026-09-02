#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
temporary_root="$(mktemp -d)"
cleanup() {
  rm -rf "$temporary_root"
}
trap cleanup EXIT

git clone --quiet --no-local "$repo_root" "$temporary_root/repository"
git -C "$temporary_root/repository" checkout --quiet "$(git -C "$repo_root" rev-parse HEAD)"

(
  cd "$temporary_root/repository"
  python3 -m venv "$temporary_root/venv"
  "$temporary_root/venv/bin/pip" install --quiet -r requirements-dev.txt
  make PYTHON="$temporary_root/venv/bin/python" test
  make PYTHON="$temporary_root/venv/bin/python" validate
  # Use the compiler's file output contract so environment/bootstrap chatter can
  # never contaminate the YAML stream captured by this independence gate.
  "$temporary_root/venv/bin/python" scripts/infra.py render \
    --catalog catalog/packages \
    --stack examples/stacks/demo.yaml \
    --profile examples/profiles/local.yaml \
    --lock versions.lock.yaml \
    --format applicationset \
    --output "$temporary_root/rendered.yaml"
  "$temporary_root/venv/bin/python" - "$temporary_root/rendered.yaml" <<'PY'
import sys
from pathlib import Path

import yaml

documents = list(yaml.safe_load_all(Path(sys.argv[1]).read_text(encoding="utf-8")))
if not documents or any(document.get("kind") != "ApplicationSet" for document in documents):
    raise SystemExit("fresh-clone render is not a pure ApplicationSet YAML stream")
PY
)

test -s "$temporary_root/rendered.yaml"
if grep -En '/Users/|\.\./|github\.com/(convee|hullwork)/' "$temporary_root/rendered.yaml"; then
  printf '%s\n' 'fresh-clone render contains a source-workspace or product-specific dependency' >&2
  exit 1
fi
printf '%s\n' 'fresh-clone plugin validation and render passed'
