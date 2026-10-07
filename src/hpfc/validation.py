"""Contrôles de la courbe : reproduction des produits cotés, synthèse mensuelle."""
from __future__ import annotations

import numpy as np
import pandas as pd


def reprice(curve: np.ndarray, cal: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Prix de chaque produit recalculé sur la courbe horaire."""
    dates = cal["date"].to_numpy()
    is_peak = cal["is_peak"].to_numpy()
    values = []
    for _, p in products.iterrows():
        m = (dates >= p["start"]) & (dates <= p["end"])
        if p["profile"] == "Peak":
            m &= is_peak
        values.append(float(curve[m].mean()))
    out = products[["profile", "kind", "name", "settlement"]].copy()
    out["curve"] = values
    out["gap"] = out["curve"] - out["settlement"]
    return out


def check_no_arbitrage(curve: np.ndarray, cal: pd.DataFrame, constraints: pd.DataFrame,
                       settle: pd.DataFrame, tol: float) -> dict:
    """Contraintes retenues exactes (1e-6), tous les produits cotés à `tol` près."""
    exact = reprice(curve, cal, constraints)["gap"].abs().max()
    allp = reprice(curve, cal, settle)["gap"].abs().max()
    if exact > 1e-6:
        raise ValueError(f"contrainte retenue non respectée ({exact:.2e})")
    if allp > tol:
        raise ValueError(f"un produit coté n'est pas reproduit à {tol} près ({allp:.4f})")
    return {"max_gap_constraints": float(exact), "max_gap_products": float(allp)}


def monthly_summary(curve: np.ndarray, cal: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"ym": (cal["year"] * 100 + cal["month"]).to_numpy(),
                       "price": curve, "is_peak": cal["is_peak"].to_numpy()})
    return pd.DataFrame({
        "base": df.groupby("ym")["price"].mean(),
        "peak": df[df["is_peak"]].groupby("ym")["price"].mean(),
        "offpeak": df[~df["is_peak"]].groupby("ym")["price"].mean(),
        "min": df.groupby("ym")["price"].min(),
        "max": df.groupby("ym")["price"].max(),
    })
