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
from pricer.hedging import FULL_DA, STRATEGIES       # noqa: E402
from pricer.journal import archive_to_github         # noqa: E402
from pricer.market import Market                     # noqa: E402
from pricer.products import (WEEKDAYS, as_swap, block, custom, eex, empty_grid,  # noqa: E402
                             quoted, read_profile_csv, weekly)
from pricer.quote import quote                       # noqa: E402
from pricer.risk import history_bounds               # noqa: E402
from pricer.settings import Settings                 # noqa: E402

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


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

# ------------------------------------------------------------------ en-tête
st.title("💶 Pricer")
st.caption(f"{mkt.summary()} · spot history {spot.index[0].tz_convert(mkt.tz):%d/%m/%Y} → "
           f"{spot.index[-1].tz_convert(mkt.tz):%d/%m/%Y} · pricer {source}")
todo = settings.missing(need_financing=True, manual_margin=False)
if todo:
    st.warning("Parameters still to fill in: " + ", ".join(todo))

# ------------------------------------------------------------------ description du deal
PRODUCTS = {"Weekly profile (grid)": "grid", "Block (Base / Peak / Offpeak)": "block",
            "Listed EEX contract": "eex", "Hourly profile (CSV)": "csv"}

c1, c2, c3 = st.columns(3)
side = c1.radio("Side", ["sell", "buy"], horizontal=True,
                format_func=lambda s: {"sell": "Sell (to client)", "buy": "Buy (from counterparty)"}[s])
settlement = c2.radio("Settlement", ["physical", "swap"], horizontal=True,
                      format_func=lambda s: {"physical": "Physical delivery", "swap": "Financial swap"}[s])
kind = PRODUCTS[c3.selectbox("Product", list(PRODUCTS))]

default_start = max(mkt.first_day, date(mkt.first_day.year + 1, 1, 1))
if kind in ("grid", "block"):
    d1, d2, d3 = st.columns(3)
    start = d1.date_input("Delivery start", value=default_start, min_value=mkt.first_day,
                          max_value=mkt.last_day, format="DD/MM/YYYY")
    end = d2.date_input("Delivery end (incl.)", value=date(default_start.year, 12, 31),
                        min_value=mkt.first_day, max_value=mkt.last_day, format="DD/MM/YYYY")
    mw = d3.number_input("Power MW", min_value=0.0, value=1.0, step=0.5)
elif kind == "eex":
    d1, d2, d3 = st.columns(3)
    eex_profile = d1.radio("EEX profile", ["Base", "Peak"], horizontal=True)
    contract = d2.selectbox("Contract", quoted(mkt, eex_profile))
    mw = d3.number_input("Power MW", min_value=0.0, value=1.0, step=0.5)

if kind == "block":
    block_profile = st.radio("Block", ["Base", "Peak", "Offpeak"], horizontal=True)

if kind == "grid":
    st.markdown("**Weekly delivery profile** — tick the delivered hours; the week applies to the whole period "
                "(a public holiday follows its weekday).")
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

    b = st.columns(5)
    b[0].button("All", on_click=fill, args=(lambda h, d, v: True,))
    b[1].button("Clear", on_click=fill, args=(lambda h, d, v: False,))
    b[2].button("Weekdays 0h-24h", on_click=fill, args=(lambda h, d, v: d < 5,))
    b[3].button("Peak Mon-Fri 8h-20h", on_click=fill, args=(lambda h, d, v: d < 5 and 8 <= h < 20,))
    b[4].button("Invert", on_click=fill, args=(lambda h, d, v: not v,))
    shown = st.session_state.grid.copy()
    shown.index = [f"{h:02d}h-{h + 1:02d}h" for h in range(24)]
    edited = st.data_editor(shown, key=f"grid_editor_{st.session_state.grid_version}", height=36 * 25,
                            column_config={d: st.column_config.CheckboxColumn(d, width="small") for d in DAYS})
    grid = edited.reset_index(drop=True).astype(bool)
    st.session_state.grid = grid.set_axis(DAYS, axis=1)
    st.caption(f"{int(grid.to_numpy().sum())} hours ticked per week")

if kind == "csv":
    upload = st.file_uploader("Hourly profile CSV (columns: horodatage with time zone, mw)", type="csv")

h1, h2 = st.columns(2)
if kind == "eex":
    h1.info("Hedging: the listed contract itself (no strategy to choose)")
    strategy = "auto"
else:
    labels = {"auto": "Auto (cheapest scenario)", **{k: k for k in (*STRATEGIES, FULL_DA)}}
    strategy = h1.selectbox("Hedging", list(labels), format_func=labels.get,
                            index=list(labels).index(settings.strategy))
margin_mode = h2.radio("Margin", ["rules", "manual"], horizontal=True,
                       format_func=lambda s: {"rules": "Rules (config/marge.yaml)", "manual": "Manual"}[s])
manual_value, manual_reason = None, ""
if margin_mode == "manual":
    m1, m2 = st.columns([1, 3])
    manual_value = m1.number_input("Margin €/MWh", min_value=0.0, value=0.0, step=0.1)
    manual_reason = m2.text_input("Reason (required)")


def secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:                                # pas de fichier de secrets
        return None


JOURNAL_TOKEN = secret("PRICER_TOKEN")              # même jeton : lecture du code et écriture du journal
label_in = st.text_input("Deal label (client, reference…) — used in the pricing journal", value="")


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


if not JOURNAL_TOKEN:
    st.caption("Automatic pricing journal on GitHub is off: add the PRICER_TOKEN secret (read and write on "
               f"{PRICER_REPO}) to archive every pricing.")
if st.button("Price", type="primary"):
    st.session_state.pop("archived", None)
    try:
        with st.spinner("Pricing (scenarios, residual risk and deviation premium on the spot history)…"):
            q_new = quote(mkt, build_product(), settings, strategy, spot, manual_value, manual_reason)
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
    st.subheader(q.product.name)
    k = st.columns(4)
    k[0].metric("Offer price", f"{q.price:,.2f} €/MWh")
    k[1].metric("Fair price", f"{q.fair_price:,.2f} €/MWh")
    k[2].metric("Volume", f"{q.volume:,.0f} MWh")
    k[3].metric("Hedging", q.context["strategy selected"])
    for msg in q.warnings:
        st.warning(msg)
    arch = st.session_state.get("archived")
    if isinstance(arch, Exception):
        st.error(f"Pricing NOT archived on GitHub: {arch}")
    elif arch is not None:
        st.success(f"Archived in the pricing journal: [{arch.folder}]({arch.url}) (commit {arch.commit[:7]})")

    st.markdown("**Price build-up**")
    st.dataframe(q.lines.round(2), use_container_width=True)
    if len(q.strategies):
        st.markdown("**Scenarios compared** — auto = the cheapest; tie: least day-ahead volume, "
                    "then fewest contracts")
        st.dataframe(q.strategies.round(2), use_container_width=True)
    st.markdown("**EEX hedge** — position equivalent to the deal: hedge = trade the opposite side")
    st.dataframe(q.hedge.table.round(2), use_container_width=True)
    with st.expander("Hedge and residual details"):
        st.dataframe(pd.Series(q.hedge_values, name="value").to_frame().round(2), use_container_width=True)
    if len(q.risk.by_month):
        with st.expander(f"Residual risk: {q.risk.cost:,.0f} € (σ {q.risk.sigma:,.0f} €, "
                         f"confidence {q.risk.confidence:.0%})"):
            st.dataframe(q.risk.by_month.round(0), use_container_width=True)
    if q.deviation is not None and len(q.deviation.by_month):
        with st.expander(f"Day-ahead deviation premium: {q.deviation.cost:,.0f} € (σ {q.deviation.sigma:,.0f} €, "
                         f"{q.deviation.method})"):
            st.dataframe(q.deviation.by_month.round(2), use_container_width=True)
            st.caption("Price-level risk of volumes bought or sold in day-ahead without EEX hedge, measured on "
                       "a naive persistence forecast (conservative proxy, 2021-2023 crisis excluded by default). "
                       "Will be replaced by the archived HPFC's measured error once the archive is long enough.")
    if len(q.financing):
        with st.expander("Funding of sourcing"):
            st.dataframe(q.financing.round(2), use_container_width=True)
    with st.expander("Margin and context"):
        st.json({"margin": {"€": q.margin.eur, "rule": q.margin.rule, **q.margin.detail}, "context": q.context},
                expanded=False)

    # -------------------------------------------------------------- sauvegarde
    st.divider()
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
            s1, s2 = st.columns(2)
            s1.download_button("Download pricing (zip)", buf.getvalue(),
                               f"pricing_{path.parent.name}_{path.name}.zip", "application/zip")
            if (archive.REPO / ".git").exists() and source.startswith("local"):
                if s2.button("Save to pricer/pricings"):
                    try:
                        saved = archive.save_quote(mkt, q, label)
                        st.success(f"Saved in {saved}")
                    except (ValueError, FileExistsError) as exc:
                        st.error(str(exc))
