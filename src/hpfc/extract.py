"""Moteur d'extraction des courbes archivées (utilisé par le notebook et l'application web)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from hpfc import archive
from hpfc.calendar import build_calendar
from hpfc.config import HPFCConfig
from marketdata import DataNotFoundError

GRANULARITIES = {"hour": None, "day": "D", "week": "W-SUN", "month": "M", "quarter": "Q", "year": "Y"}
PROFILES = ("base", "peak", "offpeak")


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def resolve_pricing(market: str, pricing, root: Path = archive.DATA_ROOT) -> list[date]:
    """"latest", une date, une liste de dates ou un tuple (début, fin) -> dates de pricing archivées."""
    available = archive.list_curves(market, root)
    if not available:
        raise DataNotFoundError(f"aucune courbe {market} archivée")
    if pricing == "latest":
        return [available[-1]]
    if isinstance(pricing, tuple):
        lo, hi = _d(pricing[0]), _d(pricing[1])
        found = [d for d in available if lo <= d <= hi]
        if not found:
            raise DataNotFoundError(f"aucune courbe entre le {lo} et le {hi}")
        return found
    wanted = [_d(p) for p in (pricing if isinstance(pricing, list) else [pricing])]
    if not wanted:
        raise DataNotFoundError("aucune date de pricing demandée")
    missing = [d for d in wanted if d not in available]
    if missing:
        near = [str(d) for d in available if abs((d - missing[0]).days) <= 7]
        raise DataNotFoundError(f"courbe(s) absente(s) de l'archive : {[str(d) for d in missing]}. "
                                f"Dates archivées proches : {near}")
    return sorted(set(wanted))


def aggregate(curve: pd.DataFrame, cfg: HPFCConfig, granularity: str = "hour", profile: str = "base",
              tz: str = "local", start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Filtre la livraison et le profil, puis agrège (moyenne horaire).

    Granularités agrégées : colonnes price, hours, complete (la période contient-elle toutes
    ses heures du profil ?). Les périodes sont toujours celles de l'heure locale du marché.
    """
    if granularity not in GRANULARITIES or profile not in PROFILES or tz not in ("local", "utc"):
        raise ValueError(f"critère invalide : {granularity!r}, {profile!r}, {tz!r}")
    local = pd.to_datetime(curve["local"])
    day = local.dt.date
    is_peak = curve["is_peak"].astype(bool)
    mask = pd.Series(True, index=curve.index)
    if start is not None:
        mask &= day >= start
    if end is not None:
        mask &= day <= end
    if profile == "peak":
        mask &= is_peak
    elif profile == "offpeak":
        mask &= ~is_peak
    sub = curve[mask]
    if sub.empty:
        raise DataNotFoundError("aucune heure ne correspond aux critères (période ou profil)")

    if granularity == "hour":
        idx = local[mask] if tz == "local" else sub.index
        return pd.DataFrame({"price": sub["price"].to_numpy(), "hours": 1}, index=pd.Index(idx, name="hour"))

    freq = GRANULARITIES[granularity]
    period = local[mask].dt.tz_localize(None).dt.to_period(freq).dt.start_time
    out = sub.assign(period=period.to_numpy()).groupby("period").agg(price=("price", "mean"),
                                                                     hours=("price", "size"))
    out.index.name = granularity
    p0 = out.index.min().date()
    p1 = pd.Period(out.index.max(), freq).end_time.date()
    full = build_calendar(cfg, p0, p1)
    fmask = (full["is_peak"] if profile == "peak" else ~full["is_peak"] if profile == "offpeak"
             else pd.Series(True, index=full.index))
    fper = pd.to_datetime(full.loc[fmask, "local"]).dt.tz_localize(None).dt.to_period(freq).dt.start_time
    out["complete"] = out["hours"].to_numpy() == fper.value_counts().reindex(out.index).to_numpy()
    return out


def extract(market: str, cfg: HPFCConfig, pricing="latest", granularity: str = "hour", profile: str = "base",
            tz: str = "local", start=None, end=None, root: Path = archive.DATA_ROOT,
            loader=None) -> tuple[pd.DataFrame, list[date], list]:
    """Renvoie (tableau, dates de pricing, périodes partielles).

    Une date : colonnes price, hours (et complete si agrégé). Plusieurs : une colonne de prix
    par date de pricing. `loader(market, date)` permet de mettre en cache le chargement des courbes.
    """
    loader = loader or (lambda m, d: archive.load_curve(m, d, root))
    dates = resolve_pricing(market, pricing, root)
    start, end = (_d(start) if start else None), (_d(end) if end else None)
    if start and end and end < start:
        raise ValueError("la fin de livraison précède le début")
    frames = {d: aggregate(loader(market, d), cfg, granularity, profile, tz, start, end) for d in dates}
    partial = sorted({p for f in frames.values() if "complete" in f for p in f.index[~f["complete"]]})
    if len(dates) == 1:
        return frames[dates[0]], dates, partial
    wide = pd.DataFrame({str(d): f["price"] for d, f in frames.items()})
    wide.columns.name = "pricing_date"
    return wide, dates, partial
