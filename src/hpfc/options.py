"""Archivage quotidien des settlements d'options power EEX (France) : data/eex/options/.

L'API EEX ne garde qu'environ 45 jours : chaque séance non archivée est perdue. À chaque lancement, toutes les
séries négociables sur la fenêtre (mois, trimestres, années) sont interrogées en une requête chacune ; chaque
séance absente de l'archive est écrite dans son propre fichier.

Règles :
- une séance n'est archivée que si elle n'est pas plus récente que la dernière séance de futures archivée : futures
  et options sont publiés ensemble, donc une séance pas encore publiée en entier n'est jamais écrite à moitié ;
- une requête en échec arrête tout, sans rien écrire (pas d'archive partielle) ;
- une séance déjà archivée n'est pas réécrite (sauf `force`).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from hpfc import archive
from marketdata import DataNotFoundError
from marketdata.eex_options import KIND_ORDER, candidates, fetch_window, request_options

log = logging.getLogger("hpfc.options")


def collect(market: str = "FR", today: date | None = None, lookback_days: int = 45, http=request_options,
            root: Path = archive.DATA_ROOT, pause: float = 1.0, force: bool = False) -> pd.DataFrame:
    """Télécharge et archive les séances d'options de [today − lookback_days, today]. Renvoie un rapport."""
    today = today or date.today()
    first = today - timedelta(days=lookback_days)
    series = sorted(set(candidates(market, first)) | set(candidates(market, today)),
                    key=lambda s: (KIND_ORDER[s.kind], s.start))
    df = fetch_window(series, first, today, http, pause)
    if df.empty:
        raise DataNotFoundError(f"aucun settlement d'option EEX {market} entre le {first} et le {today}")
    futures = archive.list_sessions(market, root)
    if not futures:
        raise DataNotFoundError(f"aucune séance de futures {market} archivée : options non archivées")
    published = max(futures)
    done = set(archive.list_option_sessions(market, root))
    rows = []
    for s, g in df.groupby("session", sort=True):
        if s > published:
            status = f"waiting (latest futures session archived: {published})"
        elif s in done and not force:
            status = "already archived"
        else:
            archive.save_options(g, market, s, root)
            status = "archived"
        rows.append({"session": s, "status": status, "series": g["name"].nunique(), "rows": len(g)})
    report = pd.DataFrame(rows)
    log.info("Options EEX : %d séance(s) archivée(s)", int((report["status"] == "archived").sum()))
    return report
