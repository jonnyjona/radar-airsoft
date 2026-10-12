#!/usr/bin/env bash
# Junta la web (site/) con los datos públicos (sin state.json, que es interno).
set -euo pipefail
rm -rf _site
mkdir -p _site/data
cp -r site/. _site/
for f in catalog.json promos.json tracked.json settings.json prices.json; do
  [ -f "data/$f" ] && cp "data/$f" _site/data/ || echo "{}" > "_site/data/$f"
done
echo "Web lista en _site/"
