"""Page « Pricing history » : tous les pricings archivés dans le journal du dépôt pricer, et leur export.

Source : pricings/journal.csv et pricings/AAAA/MM/*.json du dépôt menckley/pricer.
  - en ligne : dernier commit du dépôt, téléchargé en une seule archive avec le jeton PRICER_TOKEN ;
  - en local : le clone du dépôt pricer installé dans l'environnement (faire `git pull` pour voir les pricings
    faits dans l'application en ligne).
Les exports contiennent tous les pricings, sans filtre.
"""
from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

PRICER_REPO = "menckley/pricer"
PRICER_REF = "main"
JOURNAL = "pricings/journal.csv"


# ------------------------------------------------------------------ lecture du journal
def _token() -> str | None:
    try:
        return st.secrets.get("PRICER_TOKEN")
    except Exception:                                # pas de fichier de secrets en local
        return None


def _local_repo() -> Path | None:
    import importlib.util
    spec = importlib.util.find_spec("pricer")
    if spec is None or "pricer-dl-" in str(spec.origin):
        return None
    return Path(spec.origin).resolve().parents[2]


def _gh(path: str, accept: str, timeout: int = 60) -> requests.Response:
    r = requests.get(f"https://api.github.com/repos/{PRICER_REPO}{path}", timeout=timeout,
                     headers={"Authorization": f"Bearer {_token()}", "Accept": accept})
    if r.status_code != 200:
        raise RuntimeError(f"accès au dépôt {PRICER_REPO} refusé par GitHub (HTTP {r.status_code}) : "
                           "vérifier le jeton PRICER_TOKEN et son accès au dépôt")
    return r


@st.cache_data(ttl=60, show_spinner=False)
def head_sha() -> str:
    return _gh(f"/commits/{PRICER_REF}", "application/vnd.github.sha", timeout=20).text.strip()


def _parse(files: dict[str, bytes]) -> tuple[pd.DataFrame, dict]:
    """files : chemin relatif au dépôt → contenu. Renvoie le journal et les pricings détaillés par chemin."""
    if JOURNAL not in files:
        return pd.DataFrame(), {}
    journal = pd.read_csv(io.BytesIO(files[JOURNAL]), dtype={"id": str})
    details = {}
    for path, data in files.items():
        if path.startswith("pricings/") and path.endswith(".json"):
            details[path] = json.loads(data.decode("utf-8"))
    return journal, details


@st.cache_data(show_spinner="Reading the pricing journal…", max_entries=4)
def from_github(sha: str) -> tuple[pd.DataFrame, dict]:
    r = _gh(f"/tarball/{sha}", "application/vnd.github+json")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        for m in tar.getmembers():
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if m.isfile() and rel.startswith("pricings/"):
                files[rel] = tar.extractfile(m).read()
    return _parse(files)


def from_local(repo: Path) -> tuple[pd.DataFrame, dict]:
    base = repo / "pricings"
    files = {p.relative_to(repo).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()} \
        if base.exists() else {}
    return _parse(files)


# ------------------------------------------------------------------ exports
def _rows(details: dict, journal: pd.DataFrame, key: str) -> pd.DataFrame:
    """Tableau long : une ligne par élément `key` de chaque pricing, précédée de son identifiant."""
    out = []
    for r in journal.itertuples():
        d = details.get(r.folder)
        if d is None:
            continue
        items = d.get(key) or []
        for it in items:
            out.append({"id": r.id, "label": r.label, **(it if isinstance(it, dict) else {"value": it})})
    return pd.DataFrame(out)


def excel_bytes(journal: pd.DataFrame, details: dict) -> bytes:
    sheets = {"Pricings": journal,
              "Price build-up": _rows(details, journal, "price_build_up"),
              "Scenarios": _rows(details, journal, "strategies"),
              "EEX hedge": _rows(details, journal, "hedge"),
              "Warnings": _rows(details, journal, "warnings").rename(columns={"value": "warning"})}
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            (df if len(df) else pd.DataFrame({"info": ["none"]})).to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
    return buf.getvalue()


# ------------------------------------------------------------------ page
st.title("Pricing history")
local = _local_repo()
try:
    if _token() and local is None:
        sha = head_sha()
        journal, details = from_github(sha)
        origin = f"GitHub {PRICER_REPO}@{sha[:7]}"
    elif local is not None:
        journal, details = from_local(local)
        origin = f"local clone {local} (git pull to see the pricings made online)"
    else:
        st.error("No access to the pricing journal: add the PRICER_TOKEN secret or install the pricer locally.")
        st.stop()
except (RuntimeError, requests.RequestException, ValueError) as exc:
    st.error(f"Pricing journal not readable: {exc}")
    st.stop()

def show(df: pd.DataFrame, decimals: int = 2, index: bool = True) -> None:
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    st.dataframe(df.style.format({c: f"{{:,.{decimals}f}}" for c in num}, na_rep="—"),
                 use_container_width=True, hide_index=not index)


top = st.columns([5, 1])
top[0].caption(f"Source: {origin} · one row per click on Price.")
if top[1].button("Refresh", use_container_width=True):
    head_sha.clear()
    st.rerun()

if journal.empty:
    st.info("No pricing archived yet: price a deal on the Pricer page (with PRICER_TOKEN in read and write).")
    st.stop()

journal = journal.sort_values("priced_at_utc", ascending=False).reset_index(drop=True)
m = st.columns(4)
m[0].metric("Pricings", f"{len(journal)}")
m[1].metric("Volume priced", f"{journal['volume_mwh'].sum():,.0f} MWh")
m[2].metric("Last pricing", pd.to_datetime(journal["priced_at_utc"].iloc[0]).tz_convert("Europe/Paris")
            .strftime("%d/%m/%Y %H:%M"))
m[3].metric("Deals labelled", f"{journal['label'].nunique()}")

stamp = datetime.now(UTC).strftime("%Y%m%d")
d1, d2, _ = st.columns([1, 1, 3])
d1.download_button("Export all (CSV)", journal.to_csv(index=False).encode("utf-8"),
                   file_name=f"pricings_{stamp}.csv", mime="text/csv", use_container_width=True,
                   help="The journal, one row per pricing.")
d2.download_button("Export all (Excel)", excel_bytes(journal, details), file_name=f"pricings_{stamp}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True,
                   help="The journal plus the price build-up, the scenarios, the EEX hedge and the warnings of every "
                        "pricing (one sheet each).")

shown = journal.assign(priced=pd.to_datetime(journal["priced_at_utc"]).dt.tz_convert("Europe/Paris")
                       .dt.strftime("%d/%m/%Y %H:%M"))
cols = ["priced", "label", "product", "side", "settlement", "delivery_start", "delivery_end", "volume_mwh",
        "fair_price", "offer_price", "margin_eur", "risk_cost_eur", "hedging", "pricing_date", "pricer_version"]
st.dataframe(shown[[c for c in cols if c in shown.columns]], use_container_width=True, hide_index=True,
             column_config={"priced": "Priced (Paris)", "label": "Label", "product": "Product", "side": "Side",
                            "settlement": "Settlement", "delivery_start": "From", "delivery_end": "To",
                            "volume_mwh": st.column_config.NumberColumn("MWh", format="%.0f"),
                            "fair_price": st.column_config.NumberColumn("Fair €/MWh", format="%.2f"),
                            "offer_price": st.column_config.NumberColumn("Offer €/MWh", format="%.2f"),
                            "margin_eur": st.column_config.NumberColumn("Margin €", format="%.0f"),
                            "risk_cost_eur": st.column_config.NumberColumn("Risk €", format="%.0f"),
                            "hedging": "Hedging", "pricing_date": "Curve date", "pricer_version": "Pricer"})

# ------------------------------------------------------------------ détail d'un pricing
st.divider()
st.subheader("Pricing detail")
pick = st.selectbox("Pricing", list(journal.index),
                    format_func=lambda i: f"{shown.loc[i, 'priced']} · {journal.loc[i, 'label']} · "
                                          f"{journal.loc[i, 'offer_price']:,.2f} €/MWh")
row = journal.loc[pick]
d = details.get(row["folder"])
if d is None:
    st.warning(f"Detailed file {row['folder']} not found in the repository.")
else:
    p = d["product"]
    st.caption(f"{p['name']} · {p['side']} · {'physical' if p['physical'] else 'financial swap'} · delivery "
               f"{p['delivery_start']} → {p['delivery_end']} · HPFC of {d['curve']['pricing_date']} · "
               f"pricer {d.get('pricer_version', '—')}")
    k = st.columns(4)
    k[0].metric("Offer price", f"{d['offer_price']:,.2f} €/MWh")
    k[1].metric("Fair price", f"{d['fair_price']:,.2f} €/MWh")
    k[2].metric("Volume", f"{p['volume_mwh']:,.0f} MWh")
    k[3].metric("Hedging", d.get("hedging") or "—")
    if d.get("option"):
        o = d["option"]
        st.markdown(f"**{o['formula']}** · expiry {o['expiry']} · F {o['forward €/MWh']:.2f} €/MWh")
    if d.get("indexation"):
        st.markdown(f"**{d['indexation']['formula']}** · {d['indexation'].get('index', '')}")
    if d.get("warnings"):
        st.warning("\n".join(f"- {w}" for w in d["warnings"]))
    tabs = ["Price build-up"] + (["Legs"] if d.get("option") and d["option"].get("legs") else []) + \
           (["Scenarios"] if d.get("strategies") else []) + (["EEX hedge"] if d.get("hedge") else []) + ["Full record"]
    pane = dict(zip(tabs, st.tabs(tabs)))
    with pane["Price build-up"]:
        show(pd.DataFrame(d["price_build_up"]).set_index("item"), 2)
    if "Legs" in pane:
        with pane["Legs"]:
            show(pd.DataFrame(d["option"]["legs"]).set_index("leg"), 4)
    if "Scenarios" in pane:
        with pane["Scenarios"]:
            show(pd.DataFrame(d["strategies"]).set_index("strategy"), 2)
    if "EEX hedge" in pane:
        with pane["EEX hedge"]:
            show(pd.DataFrame(d["hedge"]), 2, index=False)
    with pane["Full record"]:
        st.json(d, expanded=False)
        st.download_button("Download this pricing (JSON)", json.dumps(d, indent=1, ensure_ascii=False).encode("utf-8"),
                           file_name=Path(row["folder"]).name, mime="application/json")
