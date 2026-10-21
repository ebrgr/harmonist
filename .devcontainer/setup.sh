#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv pip install -e '.[dev]'
make browser-assets
mkdir -p music .dev-data/config
