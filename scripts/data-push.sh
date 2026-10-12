#!/usr/bin/env bash
# Guarda data/ en la rama "data" como un único commit (así el repositorio no crece sin límite).
set -euo pipefail
cd data
rm -rf .git
git init -q -b data
git add -A
git -c user.name="radar-airsoft[bot]" -c user.email="radar-airsoft@users.noreply.github.com" \
  commit -qm "Datos $(date -u +%Y-%m-%dT%H:%MZ)"
git push -qf "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data
rm -rf .git
echo "Datos guardados en la rama data."
