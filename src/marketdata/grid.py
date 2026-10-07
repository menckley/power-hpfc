"""Grilles temporelles attendues et contrôle de complétude des séries."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from marketdata.errors import DataNotFoundError

FREQ_SECONDS = {"15min": 900, "h": 3600}


def expected_grid(start: date, end: date, tz: str, freq: str) -> pd.DatetimeIndex:
    """Pas de temps attendus (index UTC) pour les jours locaux [start, end] inclus.

    Les journées de 23 h et 25 h des changements d'heure sont gérées naturellement.
    """
    if freq not in FREQ_SECONDS:
        raise ValueError(f"freq non gérée : {freq!r}")
    t0 = pd.Timestamp(start, tz=tz)
    t1 = pd.Timestamp(end + timedelta(days=1), tz=tz)
    return pd.date_range(t0, t1, freq=freq, inclusive="left").tz_convert("UTC")


def check_grid(s: pd.Series, expected: pd.DatetimeIndex, label: str) -> None:
    """La série doit couvrir exactement la grille attendue, sans doublon ni NaN."""
    if not s.index.is_unique:
        raise DataNotFoundError(f"{label} : {int(s.index.duplicated().sum())} horodatages en double")
    missing = expected.difference(s.index)
    extra = s.index.difference(expected)
    if len(missing) or len(extra):
        raise DataNotFoundError(f"{label} : {len(missing)} pas manquants, {len(extra)} pas en trop"
                                + (f" (1er manquant : {missing[0]})" if len(missing) else ""))
    if s.isna().any():
        raise DataNotFoundError(f"{label} : {int(s.isna().sum())} valeurs manquantes (NaN)")


def to_hourly(s: pd.Series) -> pd.Series:
    """Agrège une série 15 min en horaire (moyenne arithmétique des 4 quarts d'heure).

    Une série déjà horaire est renvoyée telle quelle. L'index doit être en UTC : les
    fuseaux européens étant décalés d'heures entières, une heure UTC = une heure locale.
    """
    if len(s) < 2:
        raise ValueError("série trop courte pour déterminer son pas")
    steps = set((s.index[1:] - s.index[:-1]).total_seconds().astype(int))
    if steps == {3600}:
        return s
    if steps != {900}:
        raise DataNotFoundError(f"pas de temps inattendu : {sorted(steps)} s")
    counts = s.resample("h").count()
    if not (counts == 4).all():
        raise DataNotFoundError(f"{int((counts != 4).sum())} heures sans exactement 4 quarts d'heure")
    return s.resample("h").mean()
