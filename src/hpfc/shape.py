"""Forme historique (section 6 du notebook, méthode E de la section 8).

forme = saisonnalité mensuelle + profil intra-mensuel (+ effet pont)
- saisonnalité : écart du mois à son année, sur les années complètes de calibration ;
- profil intra-mensuel : écart de chaque heure à la moyenne de son mois, par
  (mois, type de jour, heure), estimé depuis `intra_start` ; un couple (mois, type de
  jour) sous `min_obs` observations est estimé sur le mois et ses deux voisins ;
- effet pont : écart d'un pont au jour ouvré qu'il aurait été, estimé heure par heure ;
- mise à l'échelle E : les écarts positifs (pointes) sont multipliés par
  niveau du mois / niveau historique du mois ; les écarts négatifs (creux solaire)
  ne sont jamais amplifiés.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from hpfc.config import HPFCConfig
from marketdata import DataNotFoundError

SHAPE_DAY_TYPES = ["Lundi", "Mar-Jeu", "Vendredi", "Samedi", "Dimanche-Ferie"]


def reference_day_type(weekday: int) -> str:
    """Type de jour qu'aurait eu un pont s'il avait été un jour ouvré normal."""
    return "Lundi" if weekday == 0 else ("Vendredi" if weekday == 4 else "Mar-Jeu")


def calibration_months(hist: pd.DataFrame, cfg: HPFCConfig, pricing_date: date) -> pd.DataFrame:
    """Heures utilisables : jusqu'à la séance, hors périodes exclues, mois complets seulement.

    `hist` : calendrier horaire (build_calendar) + colonne `price`.
    Ajoute `ym` (AAAAMM) et `dev` (écart à la moyenne du mois).
    """
    h = hist[hist["date"] <= pricing_date].copy()
    excluded = pd.Series(False, index=h.index)
    for s, e in cfg.excluded_periods:
        excluded |= (h["date"] >= s) & (h["date"] <= e)
    h = h[~excluded]
    if h["price"].isna().any():
        raise DataNotFoundError("prix manquants dans l'historique de calibration")
    h["ym"] = h["year"] * 100 + h["month"]
    days_seen = h.groupby("ym")["date"].nunique()
    days_in_month = pd.Series({ym: pd.Period(year=ym // 100, month=ym % 100, freq="M").days_in_month
                               for ym in days_seen.index})
    complete = days_seen.index[days_seen == days_in_month]
    out = h[h["ym"].isin(complete)].copy()
    out["dev"] = out["price"] - out.groupby("ym")["price"].transform("mean")
    return out


def estimate_seasonality(calib_m: pd.DataFrame, min_full_years: int) -> tuple[pd.Series, list[int]]:
    """Écart moyen de chaque mois à la moyenne (pondérée par les heures) de son année."""
    m_stats = calib_m.groupby(["year", "month"]).agg(mean=("price", "mean"), hours=("duration_h", "sum"))
    full_years = [int(y) for y, g in m_stats.groupby(level="year") if len(g) == 12]
    if len(full_years) < min_full_years:
        raise DataNotFoundError(f"seulement {len(full_years)} années complètes pour la saisonnalité")
    season = m_stats.loc[full_years]
    year_mean = ((season["mean"] * season["hours"]).groupby(level="year").sum()
                 / season["hours"].groupby(level="year").sum())
    dev = (season["mean"] - year_mean.reindex(season.index.get_level_values("year")).to_numpy()).unstack("month")
    return dev.mean(axis=0).rename("month_dev"), full_years


def estimate_intra(calib_m: pd.DataFrame, start: date, end: date, min_obs: int,
                   with_pont: bool = True) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Profil intra-mensuel (et effet pont) estimé sur la fenêtre [start, end[."""
    sub = calib_m[(calib_m["date"] >= start) & (calib_m["date"] < end)]
    normal = sub[sub["day_type"] != "Pont"]
    cells = pd.MultiIndex.from_product([range(1, 13), SHAPE_DAY_TYPES, range(24)],
                                       names=["month", "day_type", "hour"])
    est = (normal.groupby(["month", "day_type", "hour"])["dev"].agg(["mean", "count"])
           .reindex(cells).fillna({"count": 0}))
    est["pooled"] = False
    for m, dt in est.index[est["count"] < min_obs].droplevel("hour").unique():
        months = [(m - 2) % 12 + 1, m, m % 12 + 1]
        pooled = (normal[(normal["day_type"] == dt) & normal["month"].isin(months)]
                  .groupby("hour")["dev"].agg(["mean", "count"]))
        for h in range(24):
            est.loc[(m, dt, h), ["mean", "count"]] = pooled.loc[h, ["mean", "count"]].to_numpy()
            est.loc[(m, dt, h), "pooled"] = True
    est = est.sort_index()
    if est["mean"].isna().any() or (est["count"] < min_obs).any():
        raise DataNotFoundError(f"fenêtre {start} -> {end} : cellules insuffisantes même après regroupement")
    if not with_pont:
        return est, None

    p = sub[sub["day_type"] == "Pont"].copy()
    p["ref_type"] = p["weekday"].map(reference_day_type)
    key = pd.MultiIndex.from_arrays([p["month"], p["ref_type"], p["hour"]])
    p["delta"] = p["dev"].to_numpy() - est["mean"].reindex(key).to_numpy()
    pont = p.groupby("hour")["delta"].agg(["mean", "count"])
    if not pont.index.equals(pd.Index(range(24), name="hour")) or (pont["count"] < min_obs).any():
        raise DataNotFoundError(f"fenêtre {start} -> {end} : effet pont insuffisant")
    pont.attrs["n_days"] = int(p["date"].nunique())
    return est, pont


@dataclass
class ShapeModel:
    month_dev: pd.Series            # index 1..12
    intra: pd.DataFrame             # index (month, day_type, hour) : mean, count, pooled
    pont_delta: pd.DataFrame        # index hour : mean, count
    level_train: pd.Series          # niveau moyen historique de chaque mois calendaire
    info: dict = field(default_factory=dict)


def fit_shape(hist: pd.DataFrame, cfg: HPFCConfig, pricing_date: date) -> ShapeModel:
    calib_m = calibration_months(hist, cfg, pricing_date)
    month_dev, full_years = estimate_seasonality(calib_m, cfg.min_full_years)
    intra, pont = estimate_intra(calib_m, cfg.intra_start, pricing_date + timedelta(days=1), cfg.min_obs)
    calib_i = calib_m[calib_m["date"] >= cfg.intra_start]
    level_train = calib_i.groupby(["year", "month"])["price"].mean().groupby(level="month").mean()
    if len(level_train) != 12 or (level_train <= 0).any():
        raise DataNotFoundError(f"niveaux historiques mensuels invalides : {level_train.to_dict()}")
    info = {
        "calibration_months": int(calib_m["ym"].nunique()),
        "calibration_hours": int(calib_m["duration_h"].sum()),
        "seasonality_years": full_years,
        "intra_months": int(calib_i["ym"].nunique()),
        "intra_cells_min_obs": int(intra["count"].min()),
        "intra_pooled": sorted({(int(m), dt) for m, dt, _ in intra.index[intra["pooled"]]}),
        "pont_days": pont.attrs["n_days"],
    }
    return ShapeModel(month_dev, intra, pont, level_train, info)


def shape_parts(model: ShapeModel, cal: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(saisonnalité, profil intra-mensuel + effet pont) pour chaque pas du calendrier."""
    is_pont = (cal["day_type"] == "Pont").to_numpy()
    ref_type = np.where(is_pont, cal["weekday"].map(reference_day_type), cal["day_type"])
    md = model.month_dev.reindex(cal["month"]).to_numpy()
    key = pd.MultiIndex.from_arrays([cal["month"], ref_type, cal["hour"]])
    intra = model.intra["mean"].reindex(key).to_numpy()
    delta = np.where(is_pont, model.pont_delta["mean"].reindex(cal["hour"]).to_numpy(), 0.0)
    intra_part = intra + delta
    if np.isnan(md).any() or np.isnan(intra_part).any():
        raise ValueError("pas sans valeur de forme")
    return md, intra_part


def scale_dev(dev: np.ndarray, k) -> np.ndarray:
    """Mise à l'échelle asymétrique : pointes x k, creux x min(k, 1)."""
    return np.where(dev > 0, dev * k, dev * np.minimum(k, 1.0))
