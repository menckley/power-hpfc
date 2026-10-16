"""Application web : pricer (page d'accueil) et courbes HPFC.

En local :   streamlit run app/streamlit_app.py
En ligne :   Streamlit Community Cloud, fichier principal app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))          # paquets hpfc et marketdata de ce dépôt

import streamlit as st                          # noqa: E402

st.set_page_config(page_title="Power pricing desk", page_icon="⚡", layout="wide")

pages = [
    st.Page("pricer_page.py", title="Pricer", icon="💶", default=True),
    st.Page("hpfc_page.py", title="HPFC curves", icon="📈"),
]
st.navigation(pages).run()
