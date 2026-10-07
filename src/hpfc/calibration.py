"""Calibration de la courbe sur les contraintes de marché (sections 7 et 8 du notebook).

courbe(heure) = forme(heure) + a(jour) + 1_peak(heure) x p(jour)

a et p minimisent la somme des carrés de leurs différences premières (variation
minimale, méthode C), sous la contrainte que chaque produit retenu soit reproduit
exactement. Résolution directe du système de Karush-Kuhn-Tucker.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from hpfc.config import HPFCConfig
from hpfc.constraints import ConstraintSet
from hpfc.shape import ShapeModel, scale_dev, shape_parts

RESID_TOL = 1e-6   # EUR/MWh, même seuil que le contrôle d'absence d'arbitrage


@dataclass
class CurveSystem:
    cal: pd.DataFrame          # calendrier horaire de la courbe
    days: pd.Index             # jours de la courbe
    hour_day: np.ndarray       # indice du jour de chaque heure
    is_peak: np.ndarray
    C: np.ndarray              # contraintes sur [a, p]
    masks: list                # heures couvertes par chaque contrainte
    settlements: np.ndarray
    kkt: np.ndarray            # matrice KKT (ne dépend pas de la forme)
    names: list                # libellé de chaque contrainte (diagnostic)

    def solve(self, shape_arr: np.ndarray) -> np.ndarray:
        """Courbe calibrée pour une forme donnée."""
        rhs = np.array([s - shape_arr[m].mean() for s, m in zip(self.settlements, self.masks)])
        n = len(self.days)
        if np.isnan(rhs).any():
            raise ValueError("calibration : forme indéfinie sur une contrainte")
        sol = np.linalg.solve(self.kkt, np.concatenate([np.zeros(2 * n), rhs]))
        resid = np.abs(self.C @ sol[:2 * n] - rhs)
        if not np.isfinite(resid).all() or resid.max() > RESID_TOL:
            k = int(np.nanargmax(resid))
            raise ValueError(f"calibration : contraintes non respectées (écart max {resid.max():.2e} "
                             f"sur {self.names[k]}, conditionnement {np.linalg.cond(self.kkt):.2e})")
        a, p = sol[:n], sol[n:2 * n]
        return shape_arr + a[self.hour_day] + self.is_peak * p[self.hour_day]


def build_system(cal: pd.DataFrame, cs: ConstraintSet, dh: pd.DataFrame) -> CurveSystem:
    days = pd.Index(dh.index)
    hour_day = days.get_indexer(cal["date"])
    if (hour_day < 0).any():
        raise ValueError("heures hors du calendrier journalier")
    is_peak = cal["is_peak"].to_numpy()
    dates_h = cal["date"].to_numpy()
    H_base, H_peak = dh["base"].to_numpy(), dh["peak"].to_numpy()

    rows, masks = [], []
    for _, c in cs.constraints.iterrows():
        dm = (days >= c["start"]) & (days <= c["end"])
        hm = (dates_h >= c["start"]) & (dates_h <= c["end"])
        if c["profile"] == "Base":
            H = H_base[dm].sum()
            ra, rp = np.where(dm, H_base, 0) / H, np.where(dm, H_peak, 0) / H
            masks.append(hm)
        else:
            H = H_peak[dm].sum()
            ra = rp = np.where(dm, H_peak, 0) / H
            masks.append(hm & is_peak)
        rows.append(np.concatenate([ra, rp]))
    C = np.vstack(rows)

    n = len(days)
    D1 = np.diff(np.eye(n), n=1, axis=0)
    Q = np.zeros((2 * n, 2 * n))
    Q[:n, :n] = Q[n:, n:] = D1.T @ D1
    kkt = np.block([[2 * Q, C.T], [C, np.zeros((len(C), len(C)))]])
    names = (cs.constraints["profile"] + " " + cs.constraints["name"].astype(str)).tolist()
    return CurveSystem(cal, days, hour_day, is_peak, C, masks,
                       cs.constraints["settlement"].to_numpy(dtype=float), kkt, names)


def build_curve(model: ShapeModel, system: CurveSystem, cfg: HPFCConfig) -> tuple[np.ndarray, dict]:
    """Méthode E : amplitude des pointes indexée sur le niveau du mois, itérée jusqu'à stabilité."""
    cal = system.cal
    md, intra = shape_parts(model, cal)
    ym = (cal["year"] * 100 + cal["month"]).to_numpy()
    lvl_train_h = model.level_train.reindex(cal["month"]).to_numpy()

    level = pd.Series(system.solve(md + intra)).groupby(ym).mean()   # point de départ : méthode C
    history = []
    for it in range(1, cfg.max_iterations + 1):
        if (level <= 0).any():
            raise ValueError(f"niveau mensuel non positif : {level[level <= 0].to_dict()}")
        k_h = level.reindex(ym).to_numpy() / lvl_train_h
        curve = system.solve(md + scale_dev(intra, k_h))
        new_level = pd.Series(curve).groupby(ym).mean()
        delta = float((new_level - level).abs().max())
        history.append(delta)
        level = new_level
        if delta < cfg.level_tol:
            break
    else:
        raise ValueError(f"pas de convergence en {cfg.max_iterations} itérations : {history}")

    k_month = level / model.level_train.reindex(level.index % 100).to_numpy()
    return curve, {"iterations": len(history), "convergence": history,
                   "amplitude_factor": {int(k): round(float(v), 4) for k, v in k_month.items()}}
