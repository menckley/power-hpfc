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
import logging
from datetime import date, timedelta

import pandas as pd

from marketdata.errors import DataNotFoundError
from marketdata.grid import check_grid, expected_grid, to_hourly
from marketdata.store import partition_dir, read_parquet, write_parquet

MTU_15MIN_START = date(2025, 10, 1)   # 1re livraison du day-ahead SDAC au pas de 15 min
log = logging.getLogger("marketdata.entsoe")


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
                    mtu_switch: date = MTU_15MIN_START, allow_gaps: bool = False) -> pd.Series:
    """Prix day-ahead des jours locaux [start, end], au pas natif du marché, contrôlés.

    Le bloc ne doit pas chevaucher le changement de pas de marché.
    Avec `allow_gaps`, les pas manquants sont tolérés (l'appelant doit les compléter puis
    contrôler la grille entière) ; doublons, pas en trop et NaN restent refusés.
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
    expected = expected_grid(start, end, tz, freq)
    if allow_gaps:
        expected = expected[expected.isin(s.index)]
    check_grid(s, expected, label)
    return s


def load_day_ahead(client, area: str, start: date, end: date, tz: str,
                   root: str | None = None, market: str | None = None,
                   mtu_switch: date = MTU_15MIN_START,
                   tolerate_last_month_gaps: bool = False) -> pd.Series:
    """Prix day-ahead [start, end] au pas natif, avec archive mensuelle facultative.

    Avec `tolerate_last_month_gaps`, les pas qu'ENTSO-E ne publie pas (et absents de l'archive)
    sont tolérés dans le DERNIER mois demandé seulement (mois en cours, non utilisé en calibration) :
    ils sont signalés, jamais inventés, et retentés au prochain passage. Tout autre mois reste strict.

    Si `root` est fourni, chaque mois est archivé dans
    root/entsoe/day_ahead/market=<market>/month=AAAA-MM/day_ahead.parquet.
    Les mois absents du cache sont téléchargés un par un. Un mois en cache qui ne
    couvre pas la période demandée est rechargé depuis son premier jour.
    """
    if root is not None and not market:
        raise ValueError("market est requis pour archiver")
    months = month_chunks(start, end)
    paths = [(partition_dir(root, "entsoe/day_ahead", market=market, month=f"{s0:%Y-%m}")
              / "day_ahead.parquet") if root is not None else None for s0, _ in months]

    parts, need, archived = {}, [], {}
    for i, (s0, e0) in enumerate(months):
        expected = expected_grid(s0, e0, tz, native_freq(s0, mtu_switch))
        if paths[i] is not None and paths[i].exists():
            cached = read_parquet(paths[i])["price"]
            archived[i] = cached
            sub = cached[(cached.index >= expected[0]) & (cached.index <= expected[-1])]
            try:
                check_grid(sub, expected, f"ENTSO-E day-ahead {area} {s0} -> {e0}")
                parts[i] = sub
                continue
            except DataNotFoundError:
                pass                  # le cache ne couvre pas la demande : on recharge
        need.append(i)

    # Une requête par mois manquant. (Des requêtes annuelles perdaient un pas en fin
    # d'année via entsoe-py : 2019-12-31 00:00 absent. Les requêtes mensuelles sont validées.)
    groups = [[i] for i in need]

    for g in groups:
        first = months[g[0]][0]
        fetch_start = date(first.year, first.month, 1) if root is not None else first
        fetch_end = months[g[-1]][1]
        log.info("ENTSO-E : téléchargement %s -> %s", fetch_start, fetch_end)
        fetched = fetch_day_ahead(client, area, fetch_start, fetch_end, tz, mtu_switch,
                                  allow_gaps=True)
        # Pas absents du téléchargement : repris de l'archive du mois (jamais d'interpolation).
        old = archived.get(g[0])
        if old is not None:
            lo_f = pd.Timestamp(fetch_start, tz=tz)
            hi_f = pd.Timestamp(fetch_end + timedelta(days=1), tz=tz)
            gap = old.index.difference(fetched.index)
            gap = gap[(gap >= lo_f) & (gap < hi_f)]
            if len(gap):
                log.warning("ENTSO-E : %d pas absents du téléchargement repris de l'archive "
                            "(1er : %s)", len(gap), gap[0])
                fetched = pd.concat([fetched, old.loc[gap].astype(float)]).sort_index().rename("price")
                fetched.index.name = "utc"
        expected_f = expected_grid(fetch_start, fetch_end, tz, native_freq(fetch_start, mtu_switch))
        if tolerate_last_month_gaps and g[-1] == len(months) - 1:
            lost = expected_f.difference(fetched.index)
            if len(lost):
                log.warning("ENTSO-E : %d pas non publiés dans le dernier mois, tolérés (1er : %s)",
                            len(lost), lost[0])
                expected_f = expected_f[expected_f.isin(fetched.index)]
        check_grid(fetched, expected_f, f"ENTSO-E day-ahead {area} {fetch_start} -> {fetch_end}")
        for i in g:
            s0, e0 = months[i]
            m0 = date(s0.year, s0.month, 1) if root is not None else s0
            lo = pd.Timestamp(m0, tz=tz).tz_convert("UTC")
            hi = pd.Timestamp(e0 + timedelta(days=1), tz=tz).tz_convert("UTC")
            month_part = fetched[(fetched.index >= lo) & (fetched.index < hi)]
            if paths[i] is not None:
                write_parquet(month_part, paths[i])
            t0 = pd.Timestamp(s0, tz=tz).tz_convert("UTC")
            parts[i] = month_part[month_part.index >= t0]
    return pd.concat([parts[i] for i in range(len(months))]).sort_index()


def load_day_ahead_hourly(client, area: str, start: date, end: date, tz: str,
                          root: str | None = None, market: str | None = None,
                          mtu_switch: date = MTU_15MIN_START,
                          tolerate_last_month_gaps: bool = False) -> pd.Series:
    """Comme load_day_ahead, puis agrégation horaire (moyenne des 4 quarts d'heure).

    Avec `tolerate_last_month_gaps`, les heures incomplètes du dernier mois sont absentes du résultat.
    """
    native = load_day_ahead(client, area, start, end, tz, root, market, mtu_switch,
                            tolerate_last_month_gaps)
    switch_utc = pd.Timestamp(mtu_switch, tz=tz).tz_convert("UTC")
    regimes = [native[native.index < switch_utc], native[native.index >= switch_utc]]
    expected = expected_grid(start, end, tz, "h")
    if tolerate_last_month_gaps:
        last_month = pd.Timestamp(date(end.year, end.month, 1), tz=tz).tz_convert("UTC")
        parts = []
        for r, per_hour in zip(regimes, (1, 4)):
            if not len(r):
                continue
            n = r.groupby(r.index.floor("h")).transform("size")
            keep = (n == per_hour) | (r.index < last_month)    # heures incomplètes écartées (dernier mois)
            parts.append(r[keep].resample("h").mean().dropna())
        hourly = pd.concat(parts).sort_index().rename("price")
        expected = expected[(expected < last_month) | expected.isin(hourly.index)]
    else:
        hourly = pd.concat([to_hourly(r) for r in regimes if len(r)]).sort_index().rename("price")
    check_grid(hourly, expected, f"ENTSO-E horaire {area} {start} -> {end}")
    return hourly
