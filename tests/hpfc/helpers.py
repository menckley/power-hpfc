"""Données de test : vrai calendrier, vraie liste de contrats, prix synthétiques déterministes."""
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

from hpfc.calendar import build_calendar, day_hours
from hpfc.config import load_config
from marketdata.eex import listed_candidates

PRICING = date(2026, 10, 5)


@lru_cache
def cfg():
    return load_config("FR")


@lru_cache
def curve_cal():
    c = cfg()
    return build_calendar(c, c.curve_start(PRICING), c.curve_end(PRICING))


@lru_cache
def curve_day_hours():
    return day_hours(curve_cal())


@lru_cache
def synthetic_settlements() -> pd.DataFrame:
    """Contrats réellement listés le 5/10/2026 (moins la semaine du 9/11, non cotée ce jour-là),
    prix = moyenne d'une courbe horaire aléatoire, arrondie au centime comme les settlements EEX."""
    c, cal = cfg(), curve_cal()
    contracts = [k for k in listed_candidates("FR", PRICING, c.n_months, c.n_quarters, c.n_years,
                                              c.short_days, c.short_window_days)
                 if not (k.kind == "week" and k.start == date(2026, 11, 9))]
    true = 80 + 40 * np.random.default_rng(1).random(len(cal))
    dates, peak = cal["date"].to_numpy(), cal["is_peak"].to_numpy()
    rows = []
    for k in contracts:
        m = (dates >= k.start) & (dates <= k.end)
        if k.profile == "Peak":
            m &= peak
        rows.append(dict(profile=k.profile, kind=k.kind, start=k.start, end=k.end, name=k.name,
                         code=k.code, maturity=k.maturity, settlement=round(float(true[m].mean()), 2)))
    return pd.DataFrame(rows)


@lru_cache
def synthetic_history() -> pd.DataFrame:
    """Historique horaire sur le vrai calendrier 2019-01-01 -> 2026-10-05, prix synthétiques."""
    c = cfg()
    hist = build_calendar(c, c.history_start, PRICING, "h")
    rng = np.random.default_rng(2)
    h, m, wd = hist["hour"].to_numpy(), hist["month"].to_numpy(), hist["weekday"].to_numpy()
    hist["price"] = (60 + 20 * np.cos(2 * np.pi * (m - 1) / 12) + 15 * np.sin(np.pi * h / 24)
                     - 10 * (wd >= 5) + rng.normal(0, 15, len(h)))
    return hist
