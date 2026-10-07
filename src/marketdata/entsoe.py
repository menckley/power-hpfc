"""Prix day-ahead de la Transparency Platform ENTSO-E (via entsoe-py).

Règles (validées dans le notebook de recherche) :
- on demande toujours explicitement la résolution : avant le passage du day-ahead
  européen au pas de 15 min (1er octobre 2025), 60 min ; après, 15 min. Le mode par
  défaut d'entsoe-py renvoie en horaire le PREMIER quart d'heure de chaque heure, pas
  la moyenne : on ne l'utilise jamais ;
- entsoe-py inclut la borne de fin : on la retire ;
- chaque bloc est comparé à la grille exacte attendue (changements d'heure compris).
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

import pandas as pd

from marketdata.errors import DataNotFoundError
from marketdata.grid import check_grid, expected_grid, to_hourly
from marketdata.store import partition_dir, read_parquet, write_parquet

MTU_15MIN_START = date(2025, 10, 1)   # 1re livraison du day-ahead SDAC au pas de 15 min


def make_client(api_key: str):
    """Client entsoe-py (import différé : le module reste utilisable avec un client de test)."""
    if not api_key:
        raise DataNotFoundError("clé API ENTSO-E absente")
    from entsoe import EntsoePandasClient
    return EntsoePandasClient(api_key=api_key)


def native_freq(day: date, mtu_switch: date = MTU_15MIN_START) -> str:
    return "15min" if day >= mtu_switch else "h"


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Découpe [start, end] en mois calendaires (bornes incluses)."""
    chunks, d = [], start
    while d <= end:
        next_month = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        chunks.append((d, min(next_month - timedelta(days=1), end)))
        d = next_month
    return chunks


def fetch_day_ahead(client, area: str, start: date, end: date, tz: str,
                    mtu_switch: date = MTU_15MIN_START) -> pd.Series:
    """Prix day-ahead des jours locaux [start, end], au pas natif du marché, contrôlés.

    Le bloc ne doit pas chevaucher le changement de pas de marché.
    """
    if start > end:
        raise ValueError(f"période vide : {start} -> {end}")
    if start < mtu_switch <= end:
        raise ValueError(f"le bloc {start} -> {end} chevauche le passage au 15 min ({mtu_switch})")
    if "resolution" not in inspect.signature(client.query_day_ahead_prices).parameters:
        raise RuntimeError("la version d'entsoe-py installée ne permet pas de choisir la résolution")

    freq = native_freq(start, mtu_switch)
    label = f"ENTSO-E day-ahead {area} {start} -> {end} ({freq})"
    t0 = pd.Timestamp(start, tz=tz)
    t1 = pd.Timestamp(end + timedelta(days=1), tz=tz)
    try:
        raw = client.query_day_ahead_prices(area, start=t0, end=t1,
                                            resolution="15min" if freq == "15min" else "60min")
    except Exception as exc:
        raise DataNotFoundError(f"{label} : échec de la requête : {exc!r}") from exc
    if raw is None or len(raw) == 0:
        raise DataNotFoundError(f"{label} : réponse vide")

    s = raw.tz_convert("UTC")
    s = s[(s.index >= t0) & (s.index < t1)].astype(float).rename("price")
    s.index.name = "utc"
    check_grid(s, expected_grid(start, end, tz, freq), label)
    return s


def load_day_ahead(client, area: str, start: date, end: date, tz: str,
                   root: str | None = None, market: str | None = None,
                   mtu_switch: date = MTU_15MIN_START) -> pd.Series:
    """Prix day-ahead [start, end] au pas natif, avec archive mensuelle facultative.

    Si `root` est fourni, chaque mois est archivé dans
    root/entsoe/day_ahead/market=<market>/month=AAAA-MM/day_ahead.parquet.
    Un mois en cache qui ne couvre pas la période demandée est rechargé depuis son
    premier jour (le fichier du mois en cours s'allonge ainsi de jour en jour).
    """
    if root is not None and not market:
        raise ValueError("market est requis pour archiver")
    parts = []
    for s0, e0 in month_chunks(start, end):
        freq = native_freq(s0, mtu_switch)
        expected = expected_grid(s0, e0, tz, freq)
        label = f"ENTSO-E day-ahead {area} {s0} -> {e0}"
        path = (partition_dir(root, "entsoe/day_ahead", market=market, month=f"{s0:%Y-%m}")
                / "day_ahead.parquet") if root is not None else None

        chunk = None
        if path is not None and path.exists():
            cached = read_parquet(path)["price"]
            sub = cached[(cached.index >= expected[0]) & (cached.index <= expected[-1])]
            try:
                check_grid(sub, expected, label)
                chunk = sub
            except DataNotFoundError:
                chunk = None          # le cache ne couvre pas la demande : on recharge
        if chunk is None:
            fetch_start = date(s0.year, s0.month, 1) if path is not None else s0
            fetched = fetch_day_ahead(client, area, fetch_start, e0, tz, mtu_switch)
            if path is not None:
                write_parquet(fetched, path)
            chunk = fetched[(fetched.index >= expected[0]) & (fetched.index <= expected[-1])]
        parts.append(chunk)
    return pd.concat(parts).sort_index()


def load_day_ahead_hourly(client, area: str, start: date, end: date, tz: str,
                          root: str | None = None, market: str | None = None,
                          mtu_switch: date = MTU_15MIN_START) -> pd.Series:
    """Comme load_day_ahead, puis agrégation horaire (moyenne des 4 quarts d'heure)."""
    native = load_day_ahead(client, area, start, end, tz, root, market, mtu_switch)
    switch_utc = pd.Timestamp(mtu_switch, tz=tz).tz_convert("UTC")
    regimes = [native[native.index < switch_utc], native[native.index >= switch_utc]]
    hourly = pd.concat([to_hourly(r) for r in regimes if len(r)]).sort_index().rename("price")
    check_grid(hourly, expected_grid(start, end, tz, "h"), f"ENTSO-E horaire {area} {start} -> {end}")
    return hourly
