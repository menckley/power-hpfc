"""Précision de la HPFC : chaque courbe archivée comparée au spot day-ahead réellement réalisé.

Pour chaque courbe du jour J et chaque jour de livraison déjà réalisé J+h, on compare :
  - le prix moyen Base, Peak et Offpeak prévu par la HPFC et celui du spot réalisé (erreur de niveau) ;
  - les 23 à 25 prix horaires (erreur horaire et corrélation du profil intra-journalier : erreur de forme).

data/accuracy/market=FR/daily.parquet   une ligne par (courbe, jour livré)
data/accuracy/market=FR/summary.csv     statistiques par horizon (D+1, D+2-7, D+8-30, D+31-90, D+91-365, > 1 an)

Erreur = HPFC − spot (positive : la HPFC surestimait). Rien n'est extrapolé : seuls les jours livrés et
entièrement archivés des deux côtés sont comptés. Le calcul est refait en entier à chaque lancement (il ne
dépend que de l'archive) : il est donc reproductible et toujours cohérent avec les courbes archivées.

Lecture : sur les premiers jours (produits courts EEX Day / Weekend / Week, `products.short_days` de la
configuration), le niveau de la HPFC est ancré sur leurs settlements ; l'erreur de niveau y mesure surtout l'écart entre le marché à terme et le day-ahead. Au-delà,
elle mesure le marché à terme (mois, trimestre, année) plus la saisonnalité de la HPFC. La forme horaire
(hourly_mae, profile_corr) est le vrai apport de la HPFC, à tous les horizons.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from hpfc import archive
from hpfc.config import HPFCConfig
from marketdata import DataNotFoundError
from marketdata.store import partition_dir, read_parquet, write_parquet

BUCKETS = [(1, 1, "D+1"), (2, 7, "D+2 to D+7"), (8, 30, "D+8 to D+30"), (31, 90, "D+31 to D+90"),
           (91, 365, "D+91 to D+365"), (366, 100_000, "beyond 1 year")]


def out_dir(market: str, root=archive.DATA_ROOT):
    return partition_dir(root, "accuracy", market=market)


def last_spot_day(market: str, tz: str, root=archive.DATA_ROOT) -> date:
    """Dernier jour local dont le spot day-ahead est archivé en entier (23 à 25 heures, ou 92 à 100 quarts
    d'heure). Les mois sont parcourus du plus récent au plus ancien : un mois qui vient de commencer peut ne
    contenir aucun jour complet."""
    base = partition_dir(root, "entsoe/day_ahead", market=market)
    months = sorted((p for p in base.glob("month=*") if (p / "day_ahead.parquet").exists()), reverse=True)
    if not months:
        raise DataNotFoundError(f"aucun spot {market} archivé")
    for m in months:
        s = read_parquet(m / "day_ahead.parquet")["price"].dropna()
        counts = pd.Series(s.index.tz_convert(tz).date).value_counts()
        full = [d for d, n in counts.items() if n in (23, 24, 25, 92, 96, 100)]
        if full:
            return max(full)
    raise DataNotFoundError(f"aucun jour complet dans le spot {market} archivé")


def daily_errors(cfg: HPFCConfig, root=archive.DATA_ROOT) -> pd.DataFrame:
    market, tz = cfg.market, cfg.tz
    curves = archive.list_curves(market, root)
    if not curves:
        raise DataNotFoundError(f"aucune courbe {market} archivée")
    last = last_spot_day(market, tz, root)
    first = min(curves)
    if first >= last:
        return pd.DataFrame()
    spot = archive.load_spot(market, first, last, tz, hourly=True, root=root)
    rows = []
    for d in curves:
        if d >= last:
            continue
        c = archive.load_curve(market, d, root)
        both = pd.DataFrame({"hpfc": c["price"], "is_peak": c["is_peak"].astype(bool)}).join(
            spot.rename("spot"), how="inner")
        if both.empty:
            continue
        both["day"] = both.index.tz_convert(tz).date
        both = both[both["day"] <= last]
        for day, g in both.groupby("day"):
            if len(g) not in (23, 24, 25):
                continue
            pk = g[g["is_peak"]]
            op = g[~g["is_peak"]]
            err = g["hpfc"] - g["spot"]
            corr = float(np.corrcoef(g["hpfc"], g["spot"])[0, 1]) if g["hpfc"].std() > 0 and g["spot"].std() > 0 \
                else np.nan
            rows.append({
                "curve_date": d, "delivery_date": day, "horizon_days": (day - d).days,
                "base_hpfc": g["hpfc"].mean(), "base_spot": g["spot"].mean(),
                "peak_hpfc": pk["hpfc"].mean() if len(pk) else np.nan,
                "peak_spot": pk["spot"].mean() if len(pk) else np.nan,
                "offpeak_hpfc": op["hpfc"].mean(), "offpeak_spot": op["spot"].mean(),
                "hourly_mae": float(err.abs().mean()), "hourly_rmse": float(np.sqrt((err ** 2).mean())),
                "profile_corr": corr})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for b in ("base", "peak", "offpeak"):
        df[f"{b}_error"] = df[f"{b}_hpfc"] - df[f"{b}_spot"]
    return df.sort_values(["curve_date", "delivery_date"]).reset_index(drop=True)


def summary(df: pd.DataFrame, since: date | None = None) -> pd.DataFrame:
    """Statistiques par tranche d'horizon ; `since` : seulement les jours livrés depuis cette date."""
    if since is not None and len(df):
        df = df[df["delivery_date"] >= since]
    rows = []
    for lo, hi, label in BUCKETS:
        g = df[(df["horizon_days"] >= lo) & (df["horizon_days"] <= hi)] if len(df) else df
        if not len(g):
            rows.append({"horizon": label, "observations": 0})
            continue
        rows.append({
            "horizon": label, "observations": len(g), "curves": g["curve_date"].nunique(),
            "delivery_days": g["delivery_date"].nunique(),
            "base_bias": g["base_error"].mean(), "base_mae": g["base_error"].abs().mean(),
            "base_rmse": float(np.sqrt((g["base_error"] ** 2).mean())),
            "base_mape_pct": 100 * (g["base_error"].abs() / g["base_spot"].abs()).replace(np.inf, np.nan).mean(),
            "peak_mae": g["peak_error"].abs().mean(), "offpeak_mae": g["offpeak_error"].abs().mean(),
            "hourly_mae": g["hourly_mae"].mean(), "hourly_rmse": float(np.sqrt((g["hourly_rmse"] ** 2).mean())),
            "profile_corr": g["profile_corr"].mean(),
            "first_delivery": min(g["delivery_date"]), "last_delivery": max(g["delivery_date"])})
    return pd.DataFrame(rows).set_index("horizon")


def run(cfg: HPFCConfig, root=archive.DATA_ROOT) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = daily_errors(cfg, root)
    if df.empty:
        raise DataNotFoundError("aucun jour livré à comparer pour l'instant")
    s = summary(df)
    d = out_dir(cfg.market, root)
    write_parquet(df, d / "daily.parquet")
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "summary.csv.tmp"
    s.round(4).to_csv(tmp)
    tmp.replace(d / "summary.csv")
    return df, s


def load(market: str, root=archive.DATA_ROOT) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = out_dir(market, root)
    if not (d / "daily.parquet").exists():
        raise DataNotFoundError(f"précision de la HPFC {market} pas encore calculée")
    return read_parquet(d / "daily.parquet"), pd.read_csv(d / "summary.csv", index_col=0)
