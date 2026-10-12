"""Sube el resultado de una búsqueda en vivo a la rama "live" con la API de GitHub.

Se usa la API (un archivo nuevo por petición) en vez de git push para que varias
búsquedas a la vez no choquen entre sí.
"""
import base64
import os
import sys

import requests

repo = os.environ["GITHUB_REPOSITORY"]
token = os.environ["GITHUB_TOKEN"]
src, dest = sys.argv[1], sys.argv[2]
api = f"https://api.github.com/repos/{repo}"
h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

# crear la rama "live" la primera vez, a partir de main
if requests.get(f"{api}/git/ref/heads/live", headers=h, timeout=20).status_code == 404:
    main_sha = requests.get(f"{api}/git/ref/heads/main", headers=h, timeout=20).json()["object"]["sha"]
    requests.post(f"{api}/git/refs", headers=h, json={"ref": "refs/heads/live", "sha": main_sha}, timeout=20).raise_for_status()

with open(src, "rb") as f:
    content = base64.b64encode(f.read()).decode()
r = requests.put(f"{api}/contents/{dest}", headers=h, timeout=30, json={
    "message": f"En vivo {os.path.basename(dest)}",
    "content": content,
    "branch": "live",
})
print(r.status_code, r.json().get("content", {}).get("path") if r.ok else r.text[:300])
r.raise_for_status()
