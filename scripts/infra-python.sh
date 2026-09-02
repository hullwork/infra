#!/usr/bin/env bash
# Run infra Python CLIs with their declared dependencies.
set -euo pipefail

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "$infra_root/.venv/bin/python" ]; then
  exec "$infra_root/.venv/bin/python" "$@"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --with-requirements "$infra_root/requirements-dev.txt" python "$@"
fi
exec python3 "$@"
