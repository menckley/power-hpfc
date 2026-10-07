"""Calendrier : types de jour, jours fériés, ponts, heures peak / offpeak (section 2 du notebook)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from hpfc.config import HPFCConfig
from marketdata import DataNotFoundError

DAY_TYPES = ("Lundi", "Mar-Jeu", "Vendredi", "Samedi", "Dimanche-Ferie", "Pont")
STEP_HOURS = {"h": 1.0, "15min": 0.25}


def build_day_calendar(cfg: HPFCConfig, start: date, end: date) -> pd.DataFrame:
    """Calendrier journalier en dates locales, bornes incluses.

    Un pont est un jour ouvré coincé entre deux jours non travaillés (ex. vendredi
    de l'Ascension). Un jour férié prend le type « Dimanche-Ferie ».
    """
    hol = cfg.holiday_calendar(range(start.year - 1, end.year + 2))

    def is_off(d: date) -> bool:
        return d.weekday() >= 5 or d in hol

    rows, d = [], start
    while d <= end:
        wd = d.weekday()
        is_holiday = d in hol
        is_bridge = (wd < 5 and not is_holiday
                     and is_off(d - timedelta(days=1)) and is_off(d + timedelta(days=1)))
        if is_holiday or wd == 6:
            day_type = "Dimanche-Ferie"
        elif is_bridge:
            day_type = "Pont"
        elif wd == 5:
            day_type = "Samedi"
        elif wd == 0:
            day_type = "Lundi"
        elif wd == 4:
            day_type = "Vendredi"
        else:
            day_type = "Mar-Jeu"
        rows.append({"date": d, "weekday": wd, "is_holiday": is_holiday,
                     "holiday_name": hol.get(d, ""), "is_bridge": is_bridge, "day_type": day_type})
        d += timedelta(days=1)
    return pd.DataFrame(rows).set_index("date")


def build_calendar(cfg: HPFCConfig, start: date, end: date, freq: str = "h") -> pd.DataFrame:
    """Calendrier au pas de temps, index UTC, jours locaux [start, end] inclus."""
    if freq not in STEP_HOURS:
        raise ValueError(f"freq non gérée : {freq!r}")
    t0 = pd.Timestamp(start, tz=cfg.tz)
    t1 = pd.Timestamp(end + timedelta(days=1), tz=cfg.tz)
    local = pd.date_range(t0, t1, freq=freq, inclusive="left")

    cal = pd.DataFrame(index=local.tz_convert("UTC"))
    cal.index.name = "utc"
    cal["local"] = local
    cal["date"] = local.date
    cal["year"] = local.year
    cal["quarter"] = local.quarter
    cal["month"] = local.month
    cal["weekday"] = local.weekday
    cal["hour"] = local.hour
    cal["minute"] = local.minute
    cal["duration_h"] = STEP_HOURS[freq]

    day_info = build_day_calendar(cfg, start, end).reindex(cal["date"])
    for col in ("is_holiday", "holiday_name", "is_bridge", "day_type"):
        cal[col] = day_info[col].to_numpy()

    p = cfg.peak
    in_peak_hours = (cal["hour"] >= p.start_hour) & (cal["hour"] < p.end_hour)
    peak_day = cal["weekday"].isin(p.weekdays)
    if not p.includes_holidays:
        peak_day &= ~cal["is_holiday"].astype(bool)
    cal["is_peak"] = in_peak_hours & peak_day
    cal["is_offpeak"] = ~cal["is_peak"]
    return cal


def validate_calendar(cal: pd.DataFrame, cfg: HPFCConfig) -> None:
    """Index régulier, journées de 23/24/25 h aux bons jours, 12 h de peak par jour ouvré."""
    errors = []
    idx = cal.index
    if not idx.is_unique:
        errors.append("index non unique")
    if not idx.is_monotonic_increasing:
        errors.append("index non croissant")
    step_s = int(cal["duration_h"].iloc[0] * 3600)
    steps = set((idx[1:] - idx[:-1]).total_seconds().astype(int))
    if steps != {step_s}:
        errors.append(f"pas irrégulier : {sorted(steps)} s")

    hours_day = cal.groupby("date")["duration_h"].sum()
    if not hours_day.isin([23.0, 24.0, 25.0]).all():
        errors.append("journées de durée anormale")
    for d, h in hours_day[hours_day != 24.0].items():
        month = 3 if h == 23.0 else 10
        last_sunday = max(x for x in pd.date_range(date(d.year, month, 1), periods=31, freq="D").date
                          if x.month == month and x.weekday() == 6)
        if d != last_sunday:
            errors.append(f"{d} dure {h} h mais n'est pas un jour de changement d'heure")

    peak_day = (cal["duration_h"] * cal["is_peak"]).groupby(cal["date"]).sum()
    weekday = pd.Series([d.weekday() for d in peak_day.index], index=peak_day.index)
    n_peak = cfg.peak.end_hour - cfg.peak.start_hour
    if not (peak_day[weekday.isin(cfg.peak.weekdays)] == n_peak).all() and cfg.peak.includes_holidays:
        errors.append("un jour ouvré n'a pas le bon nombre d'heures de peak")
    if not (peak_day[~weekday.isin(cfg.peak.weekdays)] == 0).all():
        errors.append("heures de peak hors des jours de peak")
    if cal["day_type"].isna().any() or not set(cal["day_type"]) <= set(DAY_TYPES):
        errors.append("type de jour manquant ou inconnu")
    if errors:
        raise ValueError("Calendrier invalide :\n- " + "\n- ".join(errors))


def day_hours(cal: pd.DataFrame) -> pd.DataFrame:
    """Heures base / peak / offpeak de chaque jour."""
    dh = pd.DataFrame({
        "base": cal.groupby("date")["duration_h"].sum(),
        "peak": (cal["duration_h"] * cal["is_peak"]).groupby(cal["date"]).sum(),
    })
    dh["offpeak"] = dh["base"] - dh["peak"]
    return dh


def period_hours(cal: pd.DataFrame, start: date, end: date) -> pd.Series:
    """Heures base / peak / offpeak sur les jours locaux [start, end] inclus."""
    mask = (cal["date"] >= start) & (cal["date"] <= end)
    if not mask.any():
        raise DataNotFoundError(f"période {start} -> {end} hors du calendrier")
    sub = cal[mask]
    if sub["date"].iloc[0] != start or sub["date"].iloc[-1] != end:
        raise DataNotFoundError(f"période {start} -> {end} partiellement couverte par le calendrier")
    return pd.Series({
        "base": sub["duration_h"].sum(),
        "peak": sub.loc[sub["is_peak"], "duration_h"].sum(),
        "offpeak": sub.loc[sub["is_offpeak"], "duration_h"].sum(),
    })
