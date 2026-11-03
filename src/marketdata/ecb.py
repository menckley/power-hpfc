"""Taux de la BCE (API publique data-api.ecb.europa.eu, sans compte ni clé) : €STR.

Format vérifié (utilisé en production par le pricer) : CSV « csvdata », colonnes TIME_PERIOD (AAAA-MM-JJ) et
OBS_VALUE (en %). Une panne passagère (429, 5xx, réseau) est réessayée trois fois (après 2, 5 puis 10 s) ;
ensuite DataNotFoundError. Le €STR est publié chaque jour ouvré TARGET, depuis le 2 octobre 2019.
"""
from __future__ import annotations

import io
import time
from datetime import date
from typing import Callable

import pandas as pd
import requests

from marketdata.errors import DataNotFoundError

ECB_URL = "https://data-api.ecb.europa.eu/service/data/"
ESTR_KEY = "EST/B.EU000A2X2A25.WT"                 # €STR, taux moyen pondéré par les volumes
ESTR_START = date(2019, 10, 2)
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_WAITS = (2, 5, 10)


def request_csv(path: str, params: dict, sleep: Callable[[float], None] = time.sleep) -> str:
    """Texte CSV de l'API (vide si aucune observation) ; DataNotFoundError si l'API reste indisponible."""
    last = ""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            r = requests.get(ECB_URL + path, params={**params, "format": "csvdata"}, timeout=30)
        except requests.RequestException as exc:
            last = f"erreur réseau ({exc.__class__.__name__})"
        else:
            if r.status_code == 404:
                return ""
            if r.ok:
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code not in RETRY_STATUS:
                break
        if attempt < len(RETRY_WAITS):
            sleep(RETRY_WAITS[attempt])
    raise DataNotFoundError(f"API de la BCE indisponible ({last})")


def parse_estr(text: str) -> pd.Series:
    """CSV de l'API → série (index : date, valeur en %), triée ; vide si aucune observation."""
    if not text.strip():
        return pd.Series(dtype=float, name="estr_pct")
    df = pd.read_csv(io.StringIO(text))
    missing = {"TIME_PERIOD", "OBS_VALUE"} - set(df.columns)
    if missing:
        raise DataNotFoundError(f"€STR : colonnes absentes {sorted(missing)}")
    s = pd.Series(pd.to_numeric(df["OBS_VALUE"], errors="raise").astype(float).to_numpy(),
                  index=[date.fromisoformat(str(x)[:10]) for x in df["TIME_PERIOD"]], name="estr_pct")
    if s.index.duplicated().any():
        raise DataNotFoundError("€STR : plusieurs valeurs pour une même date")
    return s.sort_index()


def fetch_estr(start: date, end: date, http: Callable[[str, dict], str] = request_csv) -> pd.Series:
    """€STR publiés entre `start` et `end` inclus."""
    return parse_estr(http(ESTR_KEY, {"startPeriod": start.isoformat(), "endPeriod": end.isoformat()}))
