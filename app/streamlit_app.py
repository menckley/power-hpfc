"""Application web : pricer (page d'accueil), historique des pricings, courbes HPFC et précision de la HPFC.

En local :   streamlit run app/streamlit_app.py
En ligne :   Streamlit Community Cloud, fichier principal app/streamlit_app.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))      # paquets hpfc et marketdata de ce dépôt

import streamlit as st                          # noqa: E402

st.set_page_config(page_title="Power pricing desk", page_icon="⚡", layout="wide")


def code_signature() -> tuple:
    """Change dès qu'un fichier de src/ est modifié (push sur le dépôt, tiré par Streamlit Cloud)."""
    files = sorted((REPO / "src").rglob("*.py"))
    return len(files), max(f.stat().st_mtime_ns for f in files)


# Streamlit ne recharge que les pages : les modules hpfc et marketdata déjà importés restent ceux du démarrage.
# Après une mise à jour de src/, on les retire (ils sont réimportés à jour) et on vide les caches qui les utilisent.
_state = sys.modules.setdefault("_power_desk_code_state", types.ModuleType("_power_desk_code_state"))
_sig = code_signature()
if getattr(_state, "signature", _sig) != _sig:
    for name in [m for m in sys.modules if m.split(".")[0] in ("hpfc", "marketdata")]:
        del sys.modules[name]
    st.cache_resource.clear()
    st.cache_data.clear()
_state.signature = _sig

pages = [
    st.Page("pricer_page.py", title="Pricer", icon="💶", default=True),
    st.Page("history_page.py", title="Pricing history", icon="🗂️"),
    st.Page("hpfc_page.py", title="HPFC curves", icon="📈"),
    st.Page("accuracy_page.py", title="HPFC accuracy", icon="🎯"),
]
st.navigation(pages).run()
