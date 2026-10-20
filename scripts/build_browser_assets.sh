#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

# npm ci owns version and integrity checks; copy only the files we serve.
mkdir -p static/vendor
cp node_modules/htmx.org/dist/htmx.min.js static/vendor/htmx.min.js
cp node_modules/idiomorph/dist/idiomorph-ext.min.js static/vendor/idiomorph-ext.min.js
cp node_modules/htmx.org/LICENSE static/vendor/htmx.LICENSE
cp node_modules/idiomorph/LICENSE static/vendor/idiomorph.LICENSE
