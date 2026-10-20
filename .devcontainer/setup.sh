#!/usr/bin/env bash
set -euo pipefail
cd /workspaces/harmonist
python -m pip install -e '.[dev]'
make browser-assets
mkdir -p music .dev-data/config
