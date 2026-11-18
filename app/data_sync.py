"""Archive de données de l'application : dossier data/ du dépôt, alimenté par le dépôt privé de données.

Les données de marché (settlements et options EEX, spot ENTSO-E, €STR, courbes, précision) ne sont pas dans ce dépôt
de code : elles sont dans le dépôt privé `menckley/power-hpfc-data`, que le workflow quotidien met à jour.

- En local : data/ est un clone de ce dépôt (`git clone … data`) ; rien n'est téléchargé.
- En ligne (Streamlit Community Cloud) : au démarrage, puis à chaque nouveau commit du dépôt de données (vérifié
  toutes les 5 minutes), l'archive du dernier commit est téléchargée avec le jeton `DATA_REPO_TOKEN` des secrets de
  l'application (à défaut `PRICER_TOKEN`, s'il a aussi accès à ce dépôt) et remplace data/ d'un seul coup.
"""
from __future__ import annotations

import shutil
import tarfile
import tempfile
import threading
from pathlib import Path

import requests
import streamlit as st

DATA_REPO = "menckley/power-hpfc-data"
DATA_REF = "main"
MARKER = ".source_commit"                 # commit du dépôt de données extrait dans data/
_LOCK = threading.Lock()


def _token() -> str | None:
    for key in ("DATA_REPO_TOKEN", "PRICER_TOKEN"):
        try:
            value = st.secrets.get(key)
        except Exception:                 # pas de fichier de secrets en local
            value = None
        if value:
            return value
    return None


def _gh(path: str, accept: str, timeout: int = 60, stream: bool = False) -> requests.Response:
    r = requests.get(f"https://api.github.com/repos/{DATA_REPO}{path}", timeout=timeout, stream=stream,
                     headers={"Authorization": f"Bearer {_token()}", "Accept": accept})
    if r.status_code != 200:
        raise RuntimeError(f"accès au dépôt de données {DATA_REPO} refusé par GitHub (HTTP {r.status_code}) : "
                           "vérifier le secret DATA_REPO_TOKEN (Contents en lecture sur ce dépôt)")
    return r


@st.cache_data(ttl=300, show_spinner=False)
def head_sha() -> str:
    """Dernier commit du dépôt de données (revérifié toutes les 5 minutes)."""
    return _gh(f"/commits/{DATA_REF}", "application/vnd.github.sha", timeout=20).text.strip()


def _download(sha: str, data: Path) -> None:
    """Télécharge l'archive du commit `sha` et remplace data/ (dossier temporaire, puis renommage)."""
    parent = data.parent
    with tempfile.TemporaryDirectory(dir=parent, prefix=".data-dl-") as tmp:
        archive = Path(tmp) / "data.tar.gz"
        with _gh(f"/tarball/{sha}", "application/vnd.github+json", timeout=300, stream=True) as r, \
                open(archive, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        with tarfile.open(archive, mode="r:gz") as tar:
            members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]     # ni liens ni fichiers spéciaux
            tar.extractall(tmp, members=members, filter="data")
        root = next(p for p in Path(tmp).iterdir() if p.is_dir())               # « menckley-power-hpfc-data-<sha> »
        (root / MARKER).write_text(sha, encoding="utf-8")
        old = parent / ".data-old"
        if old.exists():
            shutil.rmtree(old)
        if data.exists():
            data.rename(old)
        root.rename(data)
        shutil.rmtree(old, ignore_errors=True)


def ensure_data(repo: Path) -> str:
    """Rend data/ disponible et à jour ; renvoie sa provenance (affichée dans l'application)."""
    data = repo / "data"
    if (data / ".git").exists():
        return "local clone of the data repository"
    if not _token():
        if data.is_dir() and any(data.iterdir()):
            return "local data folder"
        raise RuntimeError(f"no market data: clone {DATA_REPO} into data/ (local) or add the DATA_REPO_TOKEN "
                           "secret (online)")
    sha = head_sha()
    marker = data / MARKER
    if not (marker.exists() and marker.read_text(encoding="utf-8").strip() == sha):
        with _LOCK:                       # une seule session télécharge, les autres attendent
            if not (marker.exists() and marker.read_text(encoding="utf-8").strip() == sha):
                with st.spinner("Loading market data…"):
                    _download(sha, data)
    return f"GitHub {DATA_REPO}@{sha[:7]}"
