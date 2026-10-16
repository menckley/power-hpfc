"""Page « HPFC accuracy » : précision des courbes archivées face au spot day-ahead réalisé.

Lit data/accuracy/market=FR/ (écrit chaque jour par le workflow : python -m hpfc.cli accuracy).
Aucun calcul ni donnée de remplacement ici : si le fichier n'existe pas encore, la page le dit.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd                             # noqa: E402
import plotly.graph_objects as go               # noqa: E402
import streamlit as st                          # noqa: E402

from hpfc import accuracy                       # noqa: E402
from marketdata import DataNotFoundError        # noqa: E402

MARKET = "FR"
DATA = REPO / "data"
PERIODS = {"All history": None, "Last 365 days": 365, "Last 90 days": 90, "Last 30 days": 30}


def signature() -> str:
    p = accuracy.out_dir(MARKET, DATA) / "daily.parquet"
    return f"{p.stat().st_mtime_ns}" if p.exists() else ""


@st.cache_data(show_spinner=False)
def load_daily(sig: str) -> pd.DataFrame:
    df, _ = accuracy.load(MARKET, DATA)
    for c in ("curve_date", "delivery_date"):
        df[c] = pd.to_datetime(df[c]).dt.date
    return df


st.title("HPFC accuracy")
st.caption("Every archived HPFC compared with the day-ahead price that actually cleared (ENTSO-E). "
           "Error = HPFC − day-ahead: positive means the curve was too high. Only fully delivered days count.")

try:
    daily = load_daily(signature())
except DataNotFoundError as e:
    st.info(f"Not computed yet: {e}. The daily workflow fills it as soon as a curve's first delivery day "
            "has cleared.")
    st.stop()

c1, c2 = st.columns([1, 2])
period = c1.selectbox("Delivery period", list(PERIODS))
last = daily["delivery_date"].max()
since = None if PERIODS[period] is None else last - timedelta(days=PERIODS[period] - 1)
summ = accuracy.summary(daily, since=since)
view = daily if since is None else daily[daily["delivery_date"] >= since]

m = st.columns(4)
m[0].metric("Curves compared", f"{view['curve_date'].nunique()}")
m[1].metric("Delivered days", f"{view['delivery_date'].nunique()}")
m[2].metric("Last delivery", f"{last:%d/%m/%Y}")
d1 = summ.loc["D+1"] if "D+1" in summ.index and summ.loc["D+1", "observations"] > 0 else None
m[3].metric("D+1 hourly MAE", "—" if d1 is None else f"{d1['hourly_mae']:.2f} €/MWh")

st.subheader("By horizon")
table = summ.rename(columns={
    "observations": "Obs.", "curves": "Curves", "delivery_days": "Days",
    "base_bias": "Base bias", "base_mae": "Base MAE", "base_rmse": "Base RMSE", "base_mape_pct": "Base MAPE %",
    "peak_mae": "Peak MAE", "offpeak_mae": "Offpeak MAE", "hourly_mae": "Hourly MAE",
    "hourly_rmse": "Hourly RMSE", "profile_corr": "Profile corr.",
    "first_delivery": "From", "last_delivery": "To"})
st.dataframe(table, use_container_width=True,
             column_config={c: st.column_config.NumberColumn(format="%.2f")
                            for c in table.columns if c not in ("Obs.", "Curves", "Days", "From", "To")})
st.caption("€/MWh except MAPE and correlation. Bias = mean error; MAE = mean absolute error. "
           "Profile corr. = correlation of the 24 hourly prices within the day (shape quality). "
           "Short horizons are anchored on EEX Day / Weekend / Week settlements, so the level error there "
           "mostly measures forward vs day-ahead; the hourly shape is the HPFC's own contribution.")

st.subheader("Daily base: HPFC vs day-ahead")
options = [int(x) for x in sorted(view["horizon_days"].unique())]
horizon = st.select_slider("Horizon (days ahead)", options=options, value=1 if 1 in options else options[0])
h = view[view["horizon_days"] == horizon].sort_values("delivery_date")
if h.empty:
    st.info("No delivered day at this horizon in the selected period.")
else:
    fig = go.Figure()
    fig.add_scatter(x=h["delivery_date"], y=h["base_spot"], name="Day-ahead (cleared)", mode="lines+markers")
    fig.add_scatter(x=h["delivery_date"], y=h["base_hpfc"], name=f"HPFC at D-{horizon}", mode="lines+markers")
    fig.update_layout(height=380, yaxis_title="€/MWh", margin=dict(t=20, b=20),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Base error by horizon")
fig = go.Figure()
fig.add_box(x=view["horizon_days"], y=view["base_error"], name="HPFC − day-ahead", boxpoints=False)
fig.add_hline(y=0, line_dash="dot")
fig.update_layout(height=360, xaxis_title="Horizon (days)", yaxis_title="€/MWh", margin=dict(t=20, b=20))
st.plotly_chart(fig, use_container_width=True)

st.download_button("Download daily errors (CSV)", view.to_csv(index=False).encode("utf-8"),
                   file_name=f"hpfc_accuracy_{MARKET}.csv", mime="text/csv")
