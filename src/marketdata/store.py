"""Lecture / écriture Parquet et chemins d'archive partitionnés (style Hive : clé=valeur)."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def partition_dir(root: str | Path, dataset: str, **parts: str) -> Path:
    """Ex. partition_dir("data", "curves", market="FR", pricing_date="2026-10-05")
    -> data/curves/market=FR/pricing_date=2026-10-05"""
    path = Path(root) / dataset
    for key, value in parts.items():
        path = path / f"{key}={value}"
    return path


def write_parquet(df: pd.DataFrame | pd.Series, path: str | Path) -> Path:
    """Écriture atomique : fichier temporaire puis renommage (jamais de fichier à moitié écrit)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = df.to_frame() if isinstance(df, pd.Series) else df
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp)
    os.replace(tmp, path)
    return path


def read_parquet(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(Path(path))
