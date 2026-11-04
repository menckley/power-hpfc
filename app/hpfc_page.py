"""Page « HPFC curves » : extraction des courbes HPFC archivées (appelée par app/streamlit_app.py)."""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))          # le paquet n'a pas besoin d'être installé

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from hpfc import archive  # noqa: E402
from hpfc.config import load_config  # noqa: E402
from hpfc.extract import extract  # noqa: E402
from marketdata import DataNotFoundError  # noqa: E402

MARKET = "FR"
DATA = REPO / "data"
GRAN_LABELS = {"Hour": "hour", "Day": "day", "Week": "week", "Month": "month",
               "Quarter": "quarter", "Year": "year"}
PROFILE_LABELS = {"Base": "base", "Peak": "peak", "Offpeak": "offpeak"}



# ------------------------------------------------------------------ données (mises en cache)
def archive_signature() -> str:
    """Change dès qu'une courbe est ajoutée : invalide les caches après une mise à jour du dépôt."""
    return ",".join(str(d) for d in archive.list_curves(MARKET, DATA))


@st.cache_resource
def get_config():
    return load_config(MARKET)


@st.cache_data(max_entries=64, show_spinner=False)
def load_curve_cached(pricing: date, signature: str) -> pd.DataFrame:
    return archive.load_curve(MARKET, pricing, DATA)


@st.cache_data(show_spinner=False)
def load_metadata(pricing: date, signature: str) -> dict:
    return archive.load_curve_metadata(MARKET, pricing, DATA)


signature = archive_signature()
available = archive.list_curves(MARKET, DATA)
if not available:
    st.error(f"No {MARKET} curve archived in {DATA}.")
    st.stop()
cfg = get_config()
latest = available[-1]

# ------------------------------------------------------------------ critères (barre latérale)
with st.sidebar:
    st.subheader("Selection")

    mode = st.radio("Curves", ["Latest", "One date", "Several dates", "Period"], horizontal=False,
                    help=f"{len(available)} curves archived, from {available[0]} to {latest}")
    if mode == "Latest":
        pricing = "latest"
        st.caption(f"Curve of {latest}")
    elif mode == "One date":
        pricing = st.selectbox("Pricing date", available[::-1], format_func=str)
    elif mode == "Several dates":
        chosen = st.multiselect("Pricing dates", available[::-1], default=available[-2:][::-1],
                                format_func=str)
        pricing = sorted(chosen)
    else:
        c1, c2 = st.columns(2)
        p_from = c1.selectbox("From", available, index=max(0, len(available) - 10), format_func=str)
        p_to = c2.selectbox("To", available, index=len(available) - 1, format_func=str)
        pricing = (p_from, p_to)

    st.divider()
    whole = st.checkbox("Whole delivery period", value=True)
    deliv_start = deliv_end = None
    if not whole:
        ref = load_curve_cached(latest, signature)
        dmin = pd.to_datetime(ref["local"]).dt.date.min()
        dmax = pd.to_datetime(ref["local"]).dt.date.max()
        c1, c2 = st.columns(2)
        deliv_start = c1.date_input("Delivery start", value=dmin, min_value=dmin, max_value=dmax,
                                    format="DD/MM/YYYY")
        deliv_end = c2.date_input("Delivery end", value=dmax, min_value=dmin, max_value=dmax,
                                  format="DD/MM/YYYY")

    st.divider()
    gran_label = st.selectbox("Granularity", list(GRAN_LABELS), index=3)
    granularity = GRAN_LABELS[gran_label]
    profile_label = st.radio("Profile", list(PROFILE_LABELS), horizontal=True)
    profile = PROFILE_LABELS[profile_label]
    tz = "local"
    if granularity == "hour":
        tz = "utc" if st.radio("Time", ["Paris", "UTC"], horizontal=True) == "UTC" else "local"

# ------------------------------------------------------------------ extraction
st.title("HPFC curves")
st.caption(f"Latest curve {latest} · {len(available)} curves archived · updated every morning")

if isinstance(pricing, list) and not pricing:
    st.info("Select at least one pricing date in the sidebar.")
    st.stop()

try:
    with st.spinner("Extracting…"):
        result, dates, partial = extract(MARKET, cfg, pricing, granularity, profile, tz,
                                         deliv_start, deliv_end, DATA,
                                         loader=lambda m, d: load_curve_cached(d, signature))
except (DataNotFoundError, ValueError) as exc:
    st.error(str(exc))
    st.stop()

single = len(dates) == 1
price_cols = ["price"] if single else list(result.columns)

# ------------------------------------------------------------------ indicateurs
if single:
    meta = load_metadata(dates[0], signature)
    k = st.columns(5)
    k[0].metric("Pricing date", str(dates[0]))
    k[1].metric(f"Average {profile_label.lower()} price", f"{result['price'].mean():.2f} €/MWh")
    k[2].metric("Minimum", f"{result['price'].min():.2f}")
    k[3].metric("Maximum", f"{result['price'].max():.2f}")
    k[4].metric("EEX products quoted", meta.get("products", {}).get("quoted", "—"))
else:
    st.caption(f"{len(dates)} curves compared: {', '.join(map(str, dates))}")

if partial:
    st.warning("Partial periods (not all their hours are in the curve or the delivery window): "
               + ", ".join(str(p.date()) for p in partial))

# ------------------------------------------------------------------ graphique
fig = go.Figure()
scatter = go.Scattergl if granularity == "hour" else go.Scatter
shape = "linear" if granularity == "hour" else "hv"
for col in price_cols:
    fig.add_trace(scatter(x=result.index, y=result[col], mode="lines", line_shape=shape,
                          name=str(dates[0]) if col == "price" else col, line={"width": 1.4}))
fig.update_layout(height=430, margin={"l": 10, "r": 10, "t": 30, "b": 10},
                  yaxis_title="€/MWh", hovermode="x unified",
                  legend={"title": "Pricing date", "orientation": "h", "y": 1.08})
st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------------------------------ tableau et téléchargements
table = result.copy()
if isinstance(table.index, pd.DatetimeIndex) and table.index.tz is not None:
    table.index = table.index.tz_localize(None)      # Excel ne gère pas les fuseaux horaires
label_tz = "UTC" if tz == "utc" else "Paris"
table.index.name = f"{table.index.name} ({label_tz})" if granularity == "hour" else table.index.name
if not single:
    table = table.round(4)

stem = (f"hpfc_{MARKET}_{dates[0]}" if single else f"hpfc_{MARKET}_{dates[0]}_{dates[-1]}") + \
       f"_{granularity}_{profile}"
excel = io.BytesIO()
table.to_excel(excel, sheet_name="hpfc")

c1, c2, c3 = st.columns([1, 1, 4])
c1.download_button("Download CSV", table.to_csv().encode("utf-8"), f"{stem}.csv", "text/csv",
                   use_container_width=True)
c2.download_button("Download Excel", excel.getvalue(), f"{stem}.xlsx", use_container_width=True, mime=
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
st.dataframe(table, use_container_width=True, height=420)

if single:
    with st.expander("Curve metadata (checks, parameters, code version)"):
        st.code(json.dumps({k: v for k, v in meta.items() if k != "monthly"}, indent=2, ensure_ascii=False),
                language="json")
