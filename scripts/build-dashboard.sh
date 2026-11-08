#!/usr/bin/env bash
# Builds the dashboard into the Python package, so `digdown start` serves it
# and an installed copy doesn't need Node.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd web && npm ci && npm run build)
rm -rf digdown/static
cp -R web/dist digdown/static
echo "Dashboard built into digdown/static"
