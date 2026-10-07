"""Produits cotés -> contraintes indépendantes et sans arbitrage (sections 4.11 et 5 du notebook).

Entrée : un tableau de settlements avec au moins les colonnes
profile ("Base"/"Peak"), kind, start, end, name, settlement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from marketdata import DataNotFoundError
from marketdata.eex import KIND_ORDER


def coverage_gaps(settle: pd.DataFrame, start: date, end: date, profile: str,
                  peak_weekdays=(0, 1, 2, 3, 4)) -> tuple[list[date], int]:
    """Jours de [start, end] non couverts par un produit du profil (peak : jours de peak)."""
    covered = set()
    for s, e in settle.loc[settle["profile"] == profile, ["start", "end"]].itertuples(index=False):
        covered.update(pd.date_range(s, e, freq="D").date)
    needed = [d for d in pd.date_range(start, end, freq="D").date
              if profile == "Base" or d.weekday() in peak_weekdays]
    return [d for d in needed if d not in covered], len(needed)


def check_coverage(settle: pd.DataFrame, start: date, end: date, peak_weekdays=(0, 1, 2, 3, 4)) -> dict:
    """Chaque jour de la courbe doit être couvert en base, et chaque jour de peak en peak."""
    out = {}
    for profile in ("Base", "Peak"):
        gaps, n = coverage_gaps(settle, start, end, profile, peak_weekdays)
        if gaps:
            raise DataNotFoundError(f"{profile} : {len(gaps)} jours non couverts, premiers : {gaps[:5]}")
        out[profile] = n
    return out


def build_segments(settle: pd.DataFrame, dh: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Segments élémentaires : l'horizon coupé à chaque début et fin de produit."""
    cuts = {start, end + timedelta(days=1)}
    for s, e in settle[["start", "end"]].itertuples(index=False):
        cuts.update({s, e + timedelta(days=1)})
    cuts = sorted(c for c in cuts if start <= c <= end + timedelta(days=1))
    rows = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        h = dh.loc[(dh.index >= a) & (dh.index < b)].sum()
        rows.append(dict(start=a, end=b - timedelta(days=1), base_h=h["base"],
                         peak_h=h["peak"], offpeak_h=h["offpeak"]))
    seg = pd.DataFrame(rows)
    if seg["base_h"].sum() != dh.loc[(dh.index >= start) & (dh.index <= end), "base"].sum():
        raise ValueError("les segments ne couvrent pas exactement l'horizon")
    return seg


@dataclass
class ConstraintSet:
    constraints: pd.DataFrame      # produits retenus (contraintes indépendantes)
    dropped: pd.DataFrame          # produits redondants écartés, avec leur écart à l'arrondi
    segments: pd.DataFrame
    unknowns: list                 # (bloc, segment) : prix moyen peak / offpeak
    A_eq: np.ndarray
    b_eq: np.ndarray

    @property
    def degrees_of_freedom(self) -> int:
        return len(self.unknowns) - len(self.b_eq)


def _row(product, segments, unknowns_index) -> np.ndarray:
    inside = segments[(segments["start"] >= product["start"]) & (segments["end"] <= product["end"])]
    if inside.empty or inside["start"].min() != product["start"] or inside["end"].max() != product["end"]:
        raise ValueError(f"{product['name']} ne tombe pas sur des bornes de segments")
    row = np.zeros(len(unknowns_index))
    if product["profile"] == "Base":
        total = inside["base_h"].sum()
        for i, r in inside.iterrows():
            if r["peak_h"] > 0:
                row[unknowns_index[("peak", i)]] = r["peak_h"] / total
            row[unknowns_index[("offpeak", i)]] = r["offpeak_h"] / total
    else:
        total = inside["peak_h"].sum()
        if total == 0:
            raise ValueError(f"{product['name']} peak sans heure de peak")
        for i, r in inside.iterrows():
            if r["peak_h"] > 0:
                row[unknowns_index[("peak", i)]] = r["peak_h"] / total
    return row


def independent_constraints(settle: pd.DataFrame, dh: pd.DataFrame, start: date, end: date,
                            tol: float) -> ConstraintSet:
    """Garde les produits qui apportent une information nouvelle, du plus fin au plus long.

    Un produit redondant n'est écarté qu'après vérification que son prix est celui
    qu'impliquent les produits retenus, à `tol` près ; sinon : erreur d'arbitrage.
    """
    segments = build_segments(settle, dh, start, end)
    unknowns = [("peak", i) for i in segments.index if segments.at[i, "peak_h"] > 0] + \
               [("offpeak", i) for i in segments.index if segments.at[i, "offpeak_h"] > 0]
    index = {u: j for j, u in enumerate(unknowns)}

    order = (settle.assign(_k=settle["kind"].map(KIND_ORDER))
             .sort_values(["_k", "start", "profile"]).drop(columns="_k"))
    A_all = np.vstack([_row(p, segments, index) for _, p in order.iterrows()])
    b_all = order["settlement"].to_numpy(dtype=float)

    kept, dropped = [], []
    for k in range(len(b_all)):
        if kept:
            A_sel = A_all[kept]
            coef, *_ = np.linalg.lstsq(A_sel.T, A_all[k], rcond=None)
            if np.linalg.norm(A_sel.T @ coef - A_all[k]) <= 1e-9:
                implied = float(coef @ b_all[kept])
                gap = b_all[k] - implied
                p = order.iloc[k]
                if abs(gap) > tol:
                    raise ValueError(f"Arbitrage : {p['profile']} {p['name']} cote {p['settlement']} "
                                     f"mais les autres produits impliquent {implied:.4f}")
                dropped.append(dict(profile=p["profile"], name=p["name"], settlement=p["settlement"],
                                    implied=implied, gap=gap))
                continue
        kept.append(k)

    A_eq, b_eq = A_all[kept], b_all[kept]
    if np.linalg.matrix_rank(A_eq) != len(b_eq):
        raise ValueError("les contraintes retenues ne sont pas indépendantes")
    return ConstraintSet(order.iloc[kept].reset_index(drop=True), pd.DataFrame(dropped),
                         segments, unknowns, A_eq, b_eq)
