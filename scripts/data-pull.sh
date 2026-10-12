#!/usr/bin/env bash
# Descarga los datos guardados (rama "data") en la carpeta data/.
set -euo pipefail
mkdir -p data
if git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
  git fetch --depth=1 origin data
  git archive FETCH_HEAD | tar -x -C data
  echo "Datos descargados: $(ls data | tr '\n' ' ')"
else
  echo "Todavía no hay datos guardados (primera ejecución)."
fi
