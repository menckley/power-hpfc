"""Page « HPFC accuracy » : précision des courbes archivées face au spot day-ahead réalisé.

Lit data/accuracy/market=FR/ (écrit chaque jour par le workflow : python -m hpfc.cli accuracy).
Aucun calcul ni donnée de remplacement ici : si le fichier n'existe pas encore, la page le dit.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np                              # noqa: E402
import pandas as pd                             # noqa: E402
import plotly.graph_objects as go               # noqa: E402
import streamlit as st                          # noqa: E402

from hpfc import accuracy, archive              # noqa: E402
from hpfc.config import load_config             # noqa: E402
from marketdata import DataNotFoundError        # noqa: E402

MARKET = "FR"
DATA = REPO / "data"
PERIODS = {"All history": None, "Last 365 days": 365, "Last 90 days": 90, "Last 30 days": 30}
TZ = load_config(MARKET).tz
FORECAST, REALIZED = "#E8833A", "#3B82C4"          # orange : prévision HPFC ; bleu : marché réalisé
VOL_WINDOW = 30                                    # jours de référence de l'indice de volatilité
# Échelle de lecture de l'erreur horaire relative (convention de cette page, pas une norme de marché)
SCALE = [(10, "very good"), (20, "good"), (35, "fair"), (float("inf"), "poor")]


def signature() -> str:
    p = accuracy.out_dir(MARKET, DATA) / "daily.parquet"
    return f"{p.stat().st_mtime_ns}" if p.exists() else ""


@st.cache_data(show_spinner=False, max_entries=32)
def curve_day(curve_date: date, day: date) -> pd.Series:
    """Prix horaires prévus par la HPFC du `curve_date` pour le jour `day` (index en heure locale)."""
    c = archive.load_curve(MARKET, curve_date, DATA)["price"]
    c.index = c.index.tz_convert(TZ)
    return c[c.index.date == day]


@st.cache_data(show_spinner=False, max_entries=32)
def spot_window(day: date) -> pd.Series:
    """Day-ahead horaire réalisé du jour et des VOL_WINDOW jours précédents (index en heure locale)."""
    s = archive.load_spot(MARKET, day - timedelta(days=VOL_WINDOW), day, TZ, hourly=True, root=DATA)
    s.index = s.index.tz_convert(TZ)
    return s


@st.cache_data(show_spinner=False)
def load_daily(sig: str) -> pd.DataFrame:
    df, _ = accuracy.load(MARKET, DATA)
    for c in ("curve_date", "delivery_date"):
        df[c] = pd.to_datetime(df[c]).dt.date
    return df


st.title("HPFC accuracy")
st.caption("Archived HPFCs compared with the cleared day-ahead price (ENTSO-E). Error = HPFC − day-ahead; "
           "only fully delivered days count.")

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

# ------------------------------------------------------------------ vue d'un jour
st.subheader("Day view")
days = sorted(view["delivery_date"].unique(), reverse=True)
v1, v2 = st.columns(2)
day = v1.selectbox("Delivery day", days, format_func=lambda d: f"{d:%a %d/%m/%Y}")
made = view[view["delivery_date"] == day].sort_values("horizon_days")
curve_date = v2.selectbox("Forecast made on", list(made["curve_date"]),
                          format_func=lambda d: f"{d:%d/%m/%Y} (D-{(day - d).days})")
try:
    fc = curve_day(curve_date, day)
    sw = spot_window(day)
except DataNotFoundError as e:
    st.error(f"Data missing for this day: {e}")
else:
    real = sw[sw.index.date == day]
    both = pd.DataFrame({"fc": fc, "real": real}).dropna()
    err = both["fc"] - both["real"]
    mae = float(err.abs().mean())
    avg = float(both["real"].mean())
    nmae = 100 * mae / abs(avg) if abs(avg) >= 5 else None          # prix moyen proche de 0 : % sans sens
    corr = float(np.corrcoef(both["fc"], both["real"])[0, 1]) if both["fc"].std() > 0 else None
    vol = float(both["real"].std(ddof=0))
    past = sw[sw.index.date < day]
    past_vol = past.groupby(past.index.date).std(ddof=0)
    ref_vol = float(past_vol.median()) if len(past_vol) >= 20 else None
    grade = None if nmae is None else next(label for lim, label in SCALE if nmae < lim)

    fig = go.Figure()
    fig.add_scatter(x=both.index, y=both["real"], name="Realized day-ahead (market)", mode="lines+markers",
                    line=dict(color=REALIZED, width=3))
    fig.add_scatter(x=both.index, y=both["fc"], name=f"Forecast: HPFC of {curve_date:%d/%m} (D-{(day - curve_date).days})",
                    mode="lines+markers", line=dict(color=FORECAST, width=3, dash="dash"))
    fig.update_layout(height=420, yaxis_title="€/MWh", xaxis_title="Hour (local time)", hovermode="x unified",
                      margin=dict(t=30, b=20), legend=dict(orientation="h", y=1.1),
                      xaxis=dict(tickformat="%H:%M", dtick=3 * 3600 * 1000))
    st.plotly_chart(fig, use_container_width=True)

    k = st.columns(4)
    k[0].metric("Model precision", "—" if nmae is None else f"{nmae:.1f} % · {grade}",
                help="Average hourly gap between forecast and realized price, as a % of the day's average "
                     "realized price. Lower is better.")
    k[1].metric("Hourly MAE", f"{mae:.2f} €/MWh",
                help="Average absolute gap, hour by hour, between the HPFC and the realized day-ahead.")
    k[2].metric("Shape correlation", "—" if corr is None else f"{corr:.2f}",
                help="Similarity of the daily shape (solar dip, evening peak): 1 = same shape, 0 = unrelated.")
    k[3].metric("Market volatility", f"{vol:.1f} €/MWh",
                None if ref_vol is None else f"{vol / ref_vol:.1f}× its {VOL_WINDOW}-day median",
                delta_color="off",
                help=f"Standard deviation of the realized hourly day-ahead prices of the day. The comparison is "
                     f"with the median of the same index over the previous {VOL_WINDOW} days.")
    with st.expander("How to read this view"):
        st.markdown(
            "Orange dashed: what the HPFC forecast for each hour. Blue: what the day-ahead market "
        "actually cleared. **Model precision** = hourly MAE ÷ average realized price; reading scale (this page's "
        "convention, not a market standard): < 10 % very good · 10–20 % good · 20–35 % fair · > 35 % poor. "
        "**Market volatility** = how much the realized price moved within the day; on a volatile day "
        "(above 1× its median) a larger forecast error is expected. "
        f"Daily base: forecast {both['fc'].mean():.2f} vs realized {avg:.2f} €/MWh "
        f"(gap {both['fc'].mean() - avg:+.2f}). At D-1 the daily level equals the day-ahead by construction "
        "(EEX Day contract settled on the auction): only the shape differs.")

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
with st.expander("Definitions"):
    st.markdown("€/MWh except MAPE and correlation. Bias = mean error; MAE = mean absolute error. "
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
    fig.add_scatter(x=h["delivery_date"], y=h["base_spot"], name="Day-ahead (cleared)", mode="lines+markers",
                    line=dict(color=REALIZED, width=3))
    fig.add_scatter(x=h["delivery_date"], y=h["base_hpfc"], name=f"HPFC at D-{horizon}", mode="lines+markers",
                    line=dict(color=FORECAST, width=3, dash="dash"))
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
