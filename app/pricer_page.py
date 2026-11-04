"""Page « Pricer » (page d'accueil) : pricing d'un deal sur la dernière HPFC, avec le paquet du dépôt `pricer`.

En local, le paquet `pricer` installé dans l'environnement (pip install -e) est utilisé tel quel.
En ligne (Streamlit Community Cloud), il est téléchargé au démarrage depuis le dépôt privé menckley/pricer avec
le jeton `PRICER_TOKEN` des secrets de l'application (jamais écrit dans un dépôt). Le même jeton écrit le journal
des pricings dans pricings/ du dépôt pricer : il lui faut Contents en lecture et écriture sur ce seul dépôt.
"""
from __future__ import annotations

import io
import sys
import tarfile
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

REPO = Path(__file__).resolve().parents[1]          # dépôt power-hpfc (courbes, settlements, spot dans data/)
PRICER_REPO = "menckley/pricer"
PRICER_REF = "main"
MARKET = "FR"


# ------------------------------------------------------------------ paquet pricer
DL_PREFIX = "pricer-dl-"


def _token() -> str | None:
    try:
        return st.secrets.get("PRICER_TOKEN")
    except Exception:                                # pas de fichier de secrets en local
        return None


def _gh(path: str, accept: str, timeout: int = 60) -> requests.Response:
    r = requests.get(f"https://api.github.com/repos/{PRICER_REPO}{path}", timeout=timeout,
                     headers={"Authorization": f"Bearer {_token()}", "Accept": accept})
    if r.status_code != 200:
        raise RuntimeError(f"accès au dépôt {PRICER_REPO} refusé par GitHub (HTTP {r.status_code}) : "
                           "vérifier le jeton PRICER_TOKEN et son accès au dépôt")
    return r


@st.cache_data(ttl=300, show_spinner=False)
def pricer_head() -> str:
    """Version du code du pricer : derniers commits ayant modifié src/ et config/ (revérifiés toutes les
    5 minutes). Les commits du journal des pricings (pricings/) ne déclenchent donc pas de rechargement."""
    shas = []
    for path in ("src", "config"):
        r = _gh(f"/commits?sha={PRICER_REF}&path={path}&per_page=1", "application/vnd.github+json", timeout=20)
        shas.append(r.json()[0]["sha"] if r.json() else "none")
    return "-".join(s[:12] for s in shas)


@st.cache_resource(show_spinner="Chargement du pricer…", max_entries=2)
def pricer_download(version: str) -> str:
    """Code du pricer (dernier commit de la branche) extrait dans un dossier temporaire ; renvoie src/."""
    r = _gh(f"/tarball/{PRICER_REF}", "application/vnd.github+json")
    dest = Path(tempfile.mkdtemp(prefix=DL_PREFIX))
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]     # ni liens ni fichiers spéciaux
        tar.extractall(dest, members=members, filter="data")
    return str(next(dest.iterdir()) / "src")         # dossier « menckley-pricer-<commit> »


def pricer_source() -> str:
    """Rend le paquet `pricer` importable ; renvoie son origine (« local » ou le commit GitHub utilisé).

    En ligne, le code suit le dépôt : après un push qui modifie src/ ou config/ de `pricer`, la nouvelle version est
    chargée en moins de 5 minutes, sans redémarrer l'application (les modules de l'ancienne version sont
    retirés avant l'import)."""
    import importlib.util
    spec = importlib.util.find_spec("pricer")
    if spec is not None and DL_PREFIX not in str(spec.origin):
        return f"local ({Path(spec.origin).resolve().parents[2]})"
    if not _token():
        raise RuntimeError("paquet pricer introuvable : l'installer en local (pip install -e pricer) ou ajouter "
                           "le secret PRICER_TOKEN (jeton GitHub sur menckley/pricer)")
    version = pricer_head()
    src = pricer_download(version)
    if not sys.path or sys.path[0] != src:
        sys.path[:] = [x for x in sys.path if DL_PREFIX not in x]
        for name in [m for m in sys.modules if m == "pricer" or m.startswith("pricer.")]:
            del sys.modules[name]                   # ancienne version : réimportée depuis le nouveau commit
        sys.path.insert(0, src)
    import pricer  # noqa: F401
    return f"GitHub {PRICER_REPO}@{version[:7]}"


try:
    source = pricer_source()
except Exception as exc:                             # message sans le jeton
    st.error(str(exc))
    st.stop()

from pricer import archive                           # noqa: E402
from pricer.hedging import FULL_DA, STRATEGIES, hedge_summary  # noqa: E402
from pricer.journal import archive_to_github         # noqa: E402
from pricer.market import Market                     # noqa: E402
from pricer.products import (WEEKDAYS, as_swap, block, custom, eex, empty_grid,  # noqa: E402
                             quoted, read_profile_csv, weekly)
from pricer.quote import quote                       # noqa: E402
try:                                                 # pricer ≥ 0.7 : options EEX
    from pricer.option_quote import LABELS as OPTION_LABELS, option_series, quote_option  # noqa: E402
except ImportError:
    quote_option = None
try:                                                 # pricer ≥ 0.6 : contrats indexés spot
    from pricer.indexation import quote_indexed      # noqa: E402
except ImportError:
    quote_indexed = None
from pricer.risk import history_bounds               # noqa: E402
from pricer.settings import Settings                 # noqa: E402

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
from week_grid import week_grid                      # noqa: E402  (grille à cocher en glissant)


# ------------------------------------------------------------------ données (mises en cache)
def latest_curve() -> str:
    from hpfc import archive as hpfc_archive
    curves = hpfc_archive.list_curves(MARKET, REPO / "data")
    return str(curves[-1]) if curves else ""


@st.cache_resource(show_spinner="Chargement de la courbe et des settlements…")
def load_market(signature: str) -> Market:
    return Market.load(REPO, MARKET, "latest", sync=False)


@st.cache_resource(show_spinner="Chargement de l'historique spot…")
def load_spot(signature: str, start: date) -> pd.Series:
    mkt = load_market(signature)
    return mkt.spot(*history_bounds(mkt, start))


try:
    signature = f"{latest_curve()}|{source}"        # nouvelle courbe ou nouveau code du pricer : recharger
    mkt = load_market(signature)
    settings = Settings.load()
    spot = load_spot(signature, settings.history_start)
except (ValueError, LookupError, FileNotFoundError, RuntimeError) as exc:
    st.error(str(exc))
    st.stop()

# ------------------------------------------------------------------ présentation
def show(df: pd.DataFrame, decimals: int = 2, index: bool = True) -> None:
    """Tableau aux nombres alignés, séparateur de milliers, sans changer les données."""
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    st.dataframe(df.style.format({c: f"{{:,.{decimals}f}}" for c in num}, na_rep="—"),
                 use_container_width=True, hide_index=not index)


def show_items(items: dict) -> None:
    """Dictionnaire de paramètres et de méthodes en tableau à deux colonnes (au lieu de JSON brut)."""
    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}"
        if isinstance(v, (list, tuple)):
            return ", ".join(fmt(x) for x in v)
        return "—" if v is None else str(v)
    st.dataframe(pd.DataFrame({"Item": list(items), "Value": [fmt(v) for v in items.values()]}),
                 use_container_width=True, hide_index=True)


# ------------------------------------------------------------------ couverture en semaine type
COLORS = {"Base": "#2F5E9E", "Peak": "#A2543B", "Base + Peak": "#6E4A8E", "Day-ahead": "#9A6B1F",
          "Base + Day-ahead": "#2A7F78", "Peak + Day-ahead": "#8A6A2B", "Base + Peak + Day-ahead": "#3F6F5A"}


def _label_key(label: str) -> str:
    """« Base M12-26 + Day-ahead* » → « Base + Day-ahead » (clé de couleur)."""
    parts = [x.strip().split(" ")[0] for x in label.rstrip("*").split("+") if x.strip()]
    return " + ".join(parts)


def _label_color(label: str) -> str:
    return COLORS.get(_label_key(label), "#4A4F5A") if label else ""


def _legend(labels: set) -> str:
    keys = [k for k in COLORS if k in {_label_key(x) for x in labels if x}]
    chips = "".join(f'<span style="background:{COLORS[k]};color:#FFF;padding:2px 8px;margin-right:6px;'
                    f'border-radius:3px;font-size:0.8rem">{k}</span>' for k in keys)
    return f"<div style='margin:4px 0 2px 0'>{chips}</div>"


def hour_labels(h, cov: pd.DataFrame, complete: str, offset: str, with_contract: bool = False) -> pd.DataFrame:
    """Produit utilisé à chaque heure, en lecture Peak / Offpeak : un Base + Peak EEX équivaut à un Peak (heures
    peak) + un Offpeak (heures offpeak, porté par le contrat Base). Heure peak couverte : « Peak » ; heure offpeak
    couverte : « Base » ; part non couverte : « Day-ahead ». À droite, le produit dont le volume est revendu
    (ou racheté) en day-ahead à cette heure. Les MW sont dans le tableau des volumes."""
    eps = 5e-4
    idx = cov.index
    pk_hour = mkt.is_peak[idx].to_numpy()
    eex = (cov["EEX Base MW"] + cov["EEX Peak MW"]).abs().to_numpy() > eps
    has_peak = cov["EEX Peak MW"].abs().to_numpy() > eps
    product = pd.Series(["Peak" if p and k else "Base" for p, k in zip(pk_hour, has_peak)], index=idx)
    delivered = cov["delivered MW"].abs().to_numpy() > eps
    da = cov[complete].to_numpy() > eps
    use = [("" if not d else (pr + (" + Day-ahead" if a else "")) if e else "Day-ahead")
           for d, e, a, pr in zip(delivered, eex, da, product)]
    off = [pr if (o and e) else "" for o, e, pr in zip(cov[offset].to_numpy() > eps, eex, product)]
    return pd.DataFrame({"use": use, "offset": off}, index=idx)


def label_week(labels: pd.Series, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Semaine type de libellés : libellé le plus fréquent de chaque (heure, jour) sur les heures où il y en a un ;
    « * » s'il change d'une semaine à l'autre."""
    s = labels.reindex(index).fillna("")
    s = s[s != ""]
    out = pd.DataFrame("", index=[f"{x:02d}h-{x + 1:02d}h" for x in range(24)],
                       columns=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    if s.empty:
        return out
    loc = s.index.tz_convert(mkt.tz)
    df = pd.DataFrame({"l": s.to_numpy(), "h": loc.hour, "d": loc.weekday})
    for (hh, dd), g in df.groupby(["h", "d"]):
        counts = g["l"].value_counts()
        out.iat[hh, dd] = counts.index[0] + ("*" if len(counts) > 1 else "")
    return out


def _grid_style(values: pd.DataFrame, colors: pd.DataFrame):
    return values.style.apply(lambda _: colors.map(lambda c: f"background-color: {c}; color: #FFFFFF" if c else ""),
                              axis=None)


def hedge_view(q) -> None:
    """Volumes de couverture de la stratégie choisie (ou d'une autre stratégie évaluée) et leur répartition sur une
    semaine type : heures livrées par source (EEX Base, EEX Peak, day-ahead) et volumes compensés en day-ahead."""
    from pricer.hedging import coverage, full_da_hedge, hedge as build_hedge, typical_week
    chosen = q.context["strategy selected"]
    options = list(q.strategies.index) if len(q.strategies) else [chosen]
    c1, c2 = st.columns(2)
    shown = c1.selectbox("Strategy shown", options, index=options.index(chosen) if chosen in options else 0,
                         help="The strategy selected for the price, or any other strategy evaluated.")
    if shown == chosen:
        h = q.hedge
    elif shown == FULL_DA:
        h = full_da_hedge(mkt, q.product)
    else:
        h = build_hedge(mkt, q.product, shown)
    cov = coverage(mkt, q.product, h)
    if cov.empty:
        st.caption("Nothing to show.")
        return
    months = sorted(mkt.month[cov.index].unique())
    period = c2.selectbox("Period", ["Whole period"] + months)
    month = None if period == "Whole period" else period
    sel = cov if month is None else cov[mkt.month[cov.index] == month]
    sale = q.side == "sell"
    complete, offset = ("day-ahead purchase MW", "day-ahead resale MW") if sale else \
        ("day-ahead resale MW", "day-ahead purchase MW")

    vol = {("Delivered to the client" if sale else "Received from the counterparty"): sel["delivered MW"].sum(),
           "EEX Base (opposite side of the deal)": sel["EEX Base MW"].sum(),
           "EEX Peak (opposite side of the deal)": sel["EEX Peak MW"].sum(),
           "Day-ahead purchases": sel["day-ahead purchase MW"].sum(),
           "Day-ahead resales": sel["day-ahead resale MW"].sum()}
    v = pd.DataFrame({"MWh": list(vol.values())}, index=list(vol))
    v["% of delivered"] = 100 * v["MWh"] / v["MWh"].iloc[0] if v["MWh"].iloc[0] else float("nan")
    st.markdown(f"**Volumes** · {shown} · {period.lower() if month is None else period}")
    show(v, 1)

    labels = hour_labels(h, cov, complete, offset, with_contract=False)
    grid_use = label_week(labels["use"], sel.index)
    grid_off = label_week(labels["offset"], sel.index)
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Product used, per delivered hour**" if sale else "**Product used, per received hour**")
        st.dataframe(_grid_style(grid_use, grid_use.map(_label_color)), use_container_width=True, height=36 * 25)
    with g2:
        st.markdown("**Product resold in day-ahead**" if sale else "**Product bought back in day-ahead**")
        st.dataframe(_grid_style(grid_off, grid_off.map(_label_color)), use_container_width=True, height=36 * 25)
    st.markdown(_legend(set(grid_use.stack()) | set(grid_off.stack())), unsafe_allow_html=True)
    if len(h.table) and "start" in h.table.columns:
        span = sel.index if len(sel) else cov.index
        d0, d1 = mkt.day[span].min(), mkt.day[span].max()
        used = h.table[(h.table["start"] <= d1) & (h.table["end"] >= d0)]
        parts = [f"{prof} = {', '.join(map(str, used.index[used[f'{prof} MW'].abs() > 5e-4]))}"
                 for prof in ("Base", "Peak") if f"{prof} MW" in used.columns
                 and (used[f"{prof} MW"].abs() > 5e-4).any()]
        if parts:
            st.caption("EEX contracts behind these products over the period shown: " + " · ".join(parts) + ".")
    st.caption(
        ("Left: product supplying each delivered hour — Peak on peak hours, Base on offpeak hours, Day-ahead for "
         "the part no EEX product covers. Right: product whose volume is not delivered at that hour and is sold "
         "back in day-ahead." if sale else
         "Left: product offsetting each received hour — Peak on peak hours, Base on offpeak hours, Day-ahead for "
         "the part no EEX product covers. Right: product whose volume is not received at that hour and is bought "
         "back in day-ahead.")
        + " Reading in Peak / Offpeak: EEX Base + Peak held together = Peak on peak hours + Offpeak on offpeak "
          "hours (the Offpeak is carried by the Base contract, Offpeak is not listed on EEX France)."
        + " Volumes in MWh are in the table above."
        + (" * : not the same every week of the period, most frequent shown; choose a month for the exact split."
           if (grid_use.stack().str.endswith("*").any() or grid_off.stack().str.endswith("*").any()) else ""))

    if len(h.table):
        st.markdown("**EEX contracts**")
        st.caption("Position equivalent to the deal: the hedge is traded on the opposite side.")
        show(h.table, 2)
    else:
        st.caption("No EEX contract in this strategy.")
    with st.expander("Hedge and residual values"):
        show_items(q.hedge_values if shown == chosen else hedge_summary(mkt, q.product, h))


# ------------------------------------------------------------------ en-tête
st.title("Pricer")
st.caption(f"Curve {MARKET} of {mkt.pricing_date:%d/%m/%Y} · delivery {mkt.first_day:%d/%m/%Y} → "
           f"{mkt.last_day:%d/%m/%Y} · pricer {source.split(' ')[-1] if source.startswith('GitHub') else 'local'}")
with st.expander("Market data used"):
    st.write(mkt.summary())
    st.write(f"Spot history: {spot.index[0].tz_convert(mkt.tz):%d/%m/%Y} → "
             f"{spot.index[-1].tz_convert(mkt.tz):%d/%m/%Y} · pricer source: {source}")
todo = settings.missing(need_financing=True, manual_margin=False)
if todo:
    st.warning("Parameters still to fill in: " + ", ".join(todo))

# ------------------------------------------------------------------ description du deal
PRODUCTS = {"Weekly profile (grid)": "grid", "Block (Base / Peak / Offpeak)": "block",
            "Listed EEX contract": "eex", "Hourly profile (CSV)": "csv"}

PRICE_TYPES = {"fixed": "Fixed price", "indexed": "EPEX spot indexed", "option": "Option on EEX future"}
available_types = ["fixed"] + (["indexed"] if quote_indexed else []) + (["option"] if quote_option else [])

deal = st.container(border=True)
deal.markdown("**Deal**")
price_type = deal.segmented_control("Price type", available_types, default="fixed", format_func=PRICE_TYPES.get,
                                    selection_mode="single") if hasattr(deal, "segmented_control") else \
    deal.radio("Price type", available_types, horizontal=True, format_func=PRICE_TYPES.get)
price_type = price_type or "fixed"
indexed = price_type == "indexed"
is_option = price_type == "option"
c1, c2, c3 = deal.columns(3)
side = c1.radio("Side", ["sell", "buy"], horizontal=True,
                format_func=lambda s: {"sell": "Sell to client", "buy": "Buy from counterparty"}[s])
if indexed:
    c2.text_input("Settlement", "Physical delivery", disabled=True)
    settlement = "physical"
elif is_option:
    c2.text_input("Settlement", "European option on the Base future", disabled=True)
    settlement = "swap"
else:
    settlement = c2.radio("Settlement", ["physical", "swap"], horizontal=True,
                          format_func=lambda s: {"physical": "Physical", "swap": "Financial swap"}[s])
if is_option:
    kind = "option"
    try:
        series_table = option_series(mkt)
    except (LookupError, ValueError) as exc:
        series_table = None
        st.error(f"Options not available: {exc}")
    if series_table is None or series_table.empty:
        if series_table is not None:
            st.error(f"No EEX option settlements archived for the session {mkt.pricing_date}.")
        st.stop()
    structure = c3.selectbox("Structure", list(OPTION_LABELS), format_func=OPTION_LABELS.get)
    o1, o2, o3 = deal.columns(3)
    series_name = o1.selectbox("Underlying Base future", list(series_table["name"]),
                               format_func=lambda n: f"{n} · F {series_table.set_index('name').loc[n, 'settlement']:.2f}"
                                                     f" · expiry {series_table.set_index('name').loc[n, 'expiry']:%d/%m/%Y}")
    fwd = float(series_table.set_index("name").loc[series_name, "settlement"])
    if structure == "collar":
        strike_low = o2.number_input("Floor strike (put), €/MWh", min_value=0.01, value=float(round(fwd - 10)), step=1.0)
        strike_high = o3.number_input("Cap strike (call), €/MWh", min_value=0.01, value=float(round(fwd + 10)), step=1.0)
        strike_one = None
    else:
        strike_one = o2.number_input("Strike, €/MWh", min_value=0.01, value=float(round(fwd)), step=1.0)
        strike_low = strike_high = None
    mw = o3.number_input("Power, MW", min_value=0.1, value=1.0, step=0.5) if structure != "collar" else \
        deal.number_input("Power, MW", min_value=0.1, value=1.0, step=0.5)
else:
    kind = PRODUCTS[c3.selectbox("Product", list(PRODUCTS))]

default_start = max(mkt.first_day, date(mkt.first_day.year + 1, 1, 1))
if kind in ("grid", "block"):
    d1, d2, d3 = deal.columns(3)
    start = d1.date_input("Delivery start", value=default_start, min_value=mkt.first_day,
                          max_value=mkt.last_day, format="DD/MM/YYYY")
    end = d2.date_input("Delivery end (incl.)", value=date(default_start.year, 12, 31),
                        min_value=mkt.first_day, max_value=mkt.last_day, format="DD/MM/YYYY")
    mw = d3.number_input("Power, MW", min_value=0.0, value=1.0, step=0.5)
elif kind == "eex":
    d1, d2, d3 = deal.columns(3)
    eex_profile = d1.radio("EEX profile", ["Base", "Peak"], horizontal=True)
    contract = d2.selectbox("Contract", quoted(mkt, eex_profile))
    mw = d3.number_input("Power, MW", min_value=0.0, value=1.0, step=0.5)

if kind == "block":
    block_profile = deal.radio("Block", ["Base", "Peak", "Offpeak"], horizontal=True)

if kind == "grid":
    prof = st.container(border=True)
    prof.markdown("**Weekly delivery profile**")
    prof.caption("Click and drag to tick or untick a rectangle of hours. The week applies to the whole period; "
                 "a public holiday follows its weekday.")
    if "grid" not in st.session_state:
        st.session_state.grid = empty_grid().set_axis(DAYS, axis=1)
        st.session_state.grid_version = 0

    def fill(fn):
        g = st.session_state.grid.copy()
        for h in range(24):
            for d in range(7):
                g.iat[h, d] = fn(h, d, bool(g.iat[h, d]))
        st.session_state.grid = g
        st.session_state.grid_version += 1

    b = prof.columns(5)
    b[0].button("All", on_click=fill, args=(lambda h, d, v: True,), use_container_width=True)
    b[1].button("Clear", on_click=fill, args=(lambda h, d, v: False,), use_container_width=True)
    b[2].button("Weekdays", on_click=fill, args=(lambda h, d, v: d < 5,), use_container_width=True)
    b[3].button("Peak 8-20h", on_click=fill, args=(lambda h, d, v: d < 5 and 8 <= h < 20,),
                use_container_width=True)
    b[4].button("Invert", on_click=fill, args=(lambda h, d, v: not v,), use_container_width=True)
    simple = prof.toggle("Table editor", value=False,
                         help="Tick the hours one by one in a table, if the drag-and-drop grid does not display.")
    if simple:
        shown = st.session_state.grid.copy()
        shown.index = [f"{h:02d}h-{h + 1:02d}h" for h in range(24)]
        edited = prof.data_editor(shown, key=f"grid_editor_{st.session_state.grid_version}", height=36 * 25,
                                  column_config={d: st.column_config.CheckboxColumn(d, width="small") for d in DAYS})
        st.session_state.grid = edited.reset_index(drop=True).astype(bool).set_axis(DAYS, axis=1)
    else:
        with prof:
            changed = week_grid(st.session_state.grid.astype(bool).to_numpy().tolist(),
                                st.session_state.grid_version)
        if changed is not None:                      # cases cochées ou décochées à la souris
            st.session_state.grid = pd.DataFrame(changed, columns=DAYS)
    grid = st.session_state.grid.reset_index(drop=True).astype(bool)
    prof.caption(f"{int(grid.to_numpy().sum())} hours per week")

if kind == "csv":
    upload = deal.file_uploader("Hourly profile CSV (columns: horodatage with time zone, mw)", type="csv")

terms_box = st.container(border=True)
terms_box.markdown("**Pricing terms**")
h1, h2 = terms_box.columns(2)
level_method = None
vol_source = None
VOL_LABELS = {"implicite": "EEX implied", "historique": "Historical (futures)",
              "prudente": "Prudent (max when selling, min when buying)"}
LEVEL_LABELS = {"implicite": "EEX implied volatility", "historique": "Historical volatility of the futures",
                "persistance": "Persistence proxy (conservative)"}
if is_option:
    vol_default = getattr(settings, "option_vol_source", None) or "implicite"
    vol_source = h1.selectbox("Premium volatility", list(VOL_LABELS), format_func=VOL_LABELS.get,
                              index=list(VOL_LABELS).index(vol_default),
                              help="Volatility used for the offer premium. The fair premium is always shown at the "
                                   "EEX implied volatility; both are recomputed at each pricing.")
    strategy = "auto"
elif indexed:
    h1.text_input("Hedging", "None: delivered hours sourced at the index", disabled=True,
                  help="Monthly index = average EPEX day-ahead price over the delivered hours, weighted by their "
                       "MWh, rounded up to the cent.")
    strategy = "auto"
elif kind == "eex":
    h1.text_input("Hedging", "The listed contract itself", disabled=True)
    strategy = "auto"
else:
    labels = {"auto": "Auto (cheapest scenario)", **{k: k for k in (*STRATEGIES, FULL_DA)}}
    strategy = h1.selectbox("Hedging", list(labels), format_func=labels.get,
                            index=list(labels).index(settings.strategy))
    level_default = getattr(settings, "deviation_method", None) or "persistance"
    level_method = h1.selectbox("Level-risk volatility", list(LEVEL_LABELS), format_func=LEVEL_LABELS.get,
                                index=list(LEVEL_LABELS).index(level_default),
                                help="Volatility of the price level for volumes left in day-ahead without EEX "
                                     "hedge. A month without market volatility falls back on the persistence proxy "
                                     "(flagged in the Risk tab).")
margin_mode = h2.radio("Margin", ["rules", "manual"], horizontal=True,
                       format_func=lambda s: {"rules": "Rules (marge.yaml)", "manual": "Manual"}[s])
manual_value, manual_reason = None, ""
if margin_mode == "manual":
    m1, m2 = terms_box.columns([1, 3])
    manual_value = m1.number_input("Margin, €/MWh", min_value=0.0, value=0.0, step=0.1)
    manual_reason = m2.text_input("Reason (required)")


def secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:                                # pas de fichier de secrets
        return None


JOURNAL_TOKEN = secret("PRICER_TOKEN")              # même jeton : lecture du code et écriture du journal
label_in = terms_box.text_input("Deal label", value="", placeholder="Client, reference… (used in the pricing journal)")


def build_product():
    if kind == "grid":
        p = weekly(mkt, start, end, mw, grid.set_axis(list(WEEKDAYS), axis=1), side)
    elif kind == "block":
        p = block(mkt, start, end, mw, side, block_profile)
    elif kind == "eex":
        p = eex(mkt, contract, eex_profile, mw, side)
    else:
        if upload is None:
            raise ValueError("aucun fichier CSV chargé")
        q = read_profile_csv(io.BytesIO(upload.getvalue())).abs()
        p = custom(mkt, -q if side == "sell" else q,
                   f"{'Sell' if side == 'sell' else 'Buy'} hourly profile {upload.name}")
    return as_swap(p) if settlement == "swap" else p


b1, b2 = st.columns([1, 5])
priced = b1.button("Price", type="primary", use_container_width=True)
if not JOURNAL_TOKEN:
    b2.caption("Pricing journal off: add the PRICER_TOKEN secret (read and write on "
               f"{PRICER_REPO}) to archive every pricing.")
if priced:
    st.session_state.pop("archived", None)
    try:
        with st.spinner("Pricing…"):
            if is_option:
                q_new = quote_option(mkt, structure, series_name, mw, side, settings, strike=strike_one,
                                     strike_low=strike_low, strike_high=strike_high,
                                     manual_margin_eur_mwh=manual_value, manual_reason=manual_reason,
                                     vol_source=vol_source)
            elif indexed:
                q_new = quote_indexed(mkt, build_product(), settings, manual_value, manual_reason)
            else:
                q_new = quote(mkt, build_product(), settings, strategy, spot, manual_value, manual_reason,
                              level_method=level_method)
        st.session_state.quote = q_new
        st.session_state.quote_source = source
        st.session_state.label = label_in.strip() or q_new.product.name[:60]
    except (ValueError, KeyError, LookupError, FileNotFoundError) as exc:
        st.session_state.pop("quote", None)
        st.error(str(exc))
    else:
        if JOURNAL_TOKEN:
            try:
                with st.spinner(f"Archiving in {PRICER_REPO}/pricings…"):
                    st.session_state.archived = archive_to_github(mkt, q_new, st.session_state.label,
                                                                  PRICER_REPO, JOURNAL_TOKEN)
            except (RuntimeError, ValueError, requests.RequestException) as exc:
                st.session_state.archived = exc

# ------------------------------------------------------------------ résultat
q = st.session_state.get("quote")
if q is not None and st.session_state.get("quote_source") != source:
    q = None                                         # pricing fait avec une version précédente du pricer
    st.session_state.pop("quote", None)
if q is not None:
    st.divider()
    terms = getattr(q, "indexation", None)
    opt = getattr(q, "option", None)
    st.subheader(q.product.name)
    arch = st.session_state.get("archived")
    if isinstance(arch, Exception):
        st.error(f"Pricing not archived in the journal: {arch}")
    elif arch is not None:
        st.caption(f"Archived in the pricing journal: [{arch.folder}]({arch.url}) · commit {arch.commit[:7]}")

    k = st.columns(4)
    if opt is not None:
        k[0].metric("Offer premium", f"{q.price:,.3f} €/MWh", help="Paid by the client if positive (per MWh of the "
                    "underlying future).")
        k[1].metric("Fair premium (EEX vol)", f"{q.fair_price:,.3f} €/MWh")
        k[2].metric("Total premium", f"{q.price * q.volume:,.0f} €", help=f"{q.volume:,.0f} MWh underlying")
        k[3].metric("Expiry", f"{date.fromisoformat(opt['expiry']):%d/%m/%Y}", help=f"T = {opt['T years']:.4f} years "
                    f"(ACT/365) · F = {opt['forward €/MWh']:.2f} €/MWh")
    elif terms is not None:
        k[0].metric("Indexed price", terms["formula"].replace("EPEX spot day-ahead", "EPEX spot"))
        k[1].metric("Expected all-in price", f"{q.price:,.2f} €/MWh",
                    help="Index expected at the HPFC ± quoted spread: an estimate, the client pays the realised index.")
        k[2].metric("Volume", f"{q.volume:,.0f} MWh")
        k[3].metric("Exact spread", f"{terms['spread exact €/MWh']:.4f} €/MWh")
    else:
        k[0].metric("Offer price", f"{q.price:,.2f} €/MWh")
        k[1].metric("Fair price", f"{q.fair_price:,.2f} €/MWh")
        k[2].metric("Volume", f"{q.volume:,.0f} MWh")
        k[3].metric("Hedging", q.context["strategy selected"])
    if q.warnings:
        st.warning("\n".join(f"- {w}" for w in q.warnings))

    # ------------------------------------------- couverture : visible directement sous le résultat
    if opt is None and terms is None:
        with st.container(border=True):
            st.markdown("#### Hedge coverage")
            st.caption("Hourly split of the deal between EEX Base, EEX Peak and day-ahead, and the hedge volume "
                       "offset in day-ahead, on a typical week of the selected period.")
            hedge_view(q)

    # ---------------------------------------------------------- détail, par onglet
    tabs, panes = [], []
    if opt is not None:
        tabs = ["Premium build-up", "Legs and greeks", "Volatility", "Quality checks"]
    elif terms is not None:
        tabs = ["Spread build-up", "Index by month"]
    else:
        tabs = ["Price build-up"] + (["Scenarios"] if len(q.strategies) else []) + \
               (["Risk"] if len(q.risk.by_month) or (q.deviation is not None and len(q.deviation.by_month)) else [])
    if len(q.financing):
        tabs.append("Funding")
    tabs.append("Method and parameters")
    pane = dict(zip(tabs, st.tabs(tabs)))

    if opt is not None:
        with pane["Premium build-up"]:
            show(q.lines, 3)
        with pane["Legs and greeks"]:
            st.caption("Client positions. Greeks per MWh: vega per volatility point, theta per day.")
            show(opt["legs"], 4)
        with pane["Volatility"]:
            cmp = opt["comparison"]
            if "premium historical vol €/MWh" in cmp:
                c = st.columns(3)
                c[0].metric("Premium at EEX implied vol", f"{cmp['premium EEX vol €/MWh']:,.3f} €/MWh")
                c[1].metric("Premium at historical vol", f"{cmp['premium historical vol €/MWh']:,.3f} €/MWh",
                            help=f"σ {100 * opt['historical']['sigma']:.1f} % ± "
                                 f"{100 * opt['historical']['standard error']:.1f} on "
                                 f"{opt['historical']['returns']} daily returns")
                c[2].metric("Difference", f"{cmp['difference €/MWh']:+,.3f} €/MWh",
                            None if cmp.get("difference %") is None else f"{cmp['difference %']:+.1f} %",
                            delta_color="off")
            else:
                st.caption(f"Historical volatility {cmp['historical vol']}"
                           + (f" (current estimate {100 * opt['historical']['sigma estimate']:.1f} %, indicative only)"
                              if opt["historical"].get("sigma estimate") else "") + ".")
            sm = opt["smile"]
            fig = go.Figure()
            fig.add_scatter(x=sm["strike"], y=100 * sm["vol"], mode="lines+markers", name="EEX implied vol")
            for leg in opt["legs"].itertuples():
                fig.add_vline(x=leg.strike, line_dash="dot", annotation_text=leg.Index)
            if opt["historical"].get("sigma"):
                fig.add_hline(y=100 * opt["historical"]["sigma"], line_dash="dash", annotation_text="historical vol")
            fig.add_vline(x=opt["forward €/MWh"], line_color="gray", annotation_text="F")
            fig.update_layout(height=340, xaxis_title="Strike, €/MWh", yaxis_title="Implied volatility, %",
                              margin=dict(t=30, b=20), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        with pane["Quality checks"]:
            st.caption("Checks of the EEX option settlements of the session.")
            show_items(opt["checks"])
    elif terms is not None:
        with pane["Spread build-up"]:
            show(q.lines, 4)
        with pane["Index by month"]:
            st.caption(f"Expected at the HPFC; the invoice uses the realised spot. Index: {terms['index']}.")
            show(terms["by_month"], 2)
    else:
        with pane["Price build-up"]:
            show(q.lines, 2)
        if "Scenarios" in pane:
            with pane["Scenarios"]:
                st.caption("Auto selects the cheapest scenario; tie: least day-ahead volume, then fewest contracts.")
                show(q.strategies, 2)
        if "Risk" in pane:
            with pane["Risk"]:
                if len(q.risk.by_month):
                    st.markdown(f"**Residual risk** · {q.risk.cost:,.0f} € (σ {q.risk.sigma:,.0f} €, confidence "
                                f"{q.risk.confidence:.0%})")
                    show(q.risk.by_month, 0)
                if q.deviation is not None and len(q.deviation.by_month):
                    st.markdown(f"**Day-ahead level premium** · {q.deviation.cost:,.0f} € "
                                f"(σ {q.deviation.sigma:,.0f} €, {q.deviation.method})")
                    show(q.deviation.by_month, 2)
                    st.caption("Price-level risk of volumes bought or sold in day-ahead without EEX hedge. Market "
                               "volatility (implied or historical): level σ = √(exp(σ²τ) − 1), τ to mid-month. "
                               "Persistence proxy: error of a naive forecast on the spot history (2021-2023 crisis "
                               "excluded), used for the months without market volatility.")
    if "Funding" in pane:
        with pane["Funding"]:
            st.caption("Funding of the sourcing: paid to the market before the client pays.")
            show(q.financing, 2)
    with pane["Method and parameters"]:
        st.markdown("**Margin**")
        show_items({"€": q.margin.eur, "rule": q.margin.rule, **q.margin.detail})
        st.markdown("**Methods and parameters used**")
        show_items(q.context)

    # -------------------------------------------------------------- sauvegarde
    label = st.session_state.get("label", q.product.name[:60])
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = archive.save_quote(mkt, q, label, root=Path(tmp))
        except ValueError as exc:
            st.error(str(exc))
        else:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(path.iterdir()):
                    z.write(f, f"{path.parent.name}_{path.name}/{f.name}")
            s1, s2, _ = st.columns([1, 1, 3])
            s1.download_button("Download detailed pricing", buf.getvalue(),
                               f"pricing_{path.parent.name}_{path.name}.zip", "application/zip",
                               use_container_width=True)
            if (archive.REPO / ".git").exists() and source.startswith("local"):
                if s2.button("Save to pricer/pricings", use_container_width=True):
                    try:
                        saved = archive.save_quote(mkt, q, label)
                        st.success(f"Saved in {saved}")
                    except (ValueError, FileExistsError) as exc:
                        st.error(str(exc))
