#!/usr/bin/env bash
# Editable-install all TracePilot Python packages in dependency order.
# Internal `tracepilot-*` deps resolve from the already-installed editable
# packages; third-party deps resolve from PyPI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PACKAGES=(
  "packages/shared"
  "packages/prompts"
  "packages/tooling"
  "packages/retrieval"
  "packages/agent-graph"
  "packages/evals"
  "apps/api"
)

for pkg in "${PACKAGES[@]}"; do
  echo "==> pip install -e $pkg"
  pip install -e "$pkg"
done

echo "All TracePilot packages installed."
