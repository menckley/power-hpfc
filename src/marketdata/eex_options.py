"""Settlements des options power EEX via l'API publique du site eex.com (requête « table-data-option »).

Même principe que marketdata.eex pour les futures : on reproduit la requête de la page eex.com (aucun compte,
aucune clé). API non documentée, historique d'environ 45 jours.

Vérifié le 27/10/2026 pour la France (réponses réelles) :
- codes : O7FM (options sur le future Base mois), O7FQ (trimestre), O7FY (année) ; `maturity` = premier mois de
  livraison (AAAAMM), comme pour les futures ;
- une fenêtre startDate → endDate renvoie toutes les séances de la fenêtre, une ligne par (séance, strike) ;
- chaque ligne porte le settlement du call et du put, leurs volumes et leurs open interests ;
- parité call − put = F − K exacte (sans actualisation) sur le Cal-27 du 26/10/2026 : 79 strikes, F = 121,35 ;
- les options power EEX portent sur les futures **Base** seulement (spécifications EEX) ; la requête Peak (O7PM)
  ne renvoie pas de grille de strikes et n'est pas archivée.
Les volumes et open interests étaient nuls : les settlements sont des valorisations EEX (nappe de volatilité), pas
des prix traités. Ils sont archivés tels quels, avec leurs volumes, pour que la qualité puisse être jugée.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial

import pandas as pd

from marketdata.eex import HttpGet, add_months, request_table
from marketdata.errors import DataNotFoundError

OPTION_URL = "https://api.eex-group.com/pub/market-data/table-data-option"
REQUIRED = ("shortCode", "strikePrice", "maturityDate", "tradeDate", "callSettlPx", "putSettlPx")
OPTIONAL = {"callTotVolTrdd": "call_volume", "putTotVolTrdd": "put_volume",
            "callGrossOpenInt": "call_open_interest", "putGrossOpenInt": "put_open_interest"}
CODES = {"FR": {"month": "O7FM", "quarter": "O7FQ", "year": "O7FY"}}
# Maturités négociables (spécifications EEX) : 9 prochains mois, 6 prochains trimestres, 4 prochaines années
N_MONTHS, N_QUARTERS, N_YEARS = 9, 6, 4
KIND_ORDER = {"month": 0, "quarter": 1, "year": 2}

request_options = partial(request_table, url=OPTION_URL)
log = logging.getLogger("marketdata.eex_options")


@dataclass(frozen=True)
class OptionSeries:
    """Toutes les options (calls et puts, tous strikes) sur un future Base mois, trimestre ou année."""
    area: str
    kind: str
    start: date
    end: date
    name: str
    code: str
    maturity: str

    def params(self, window_start: date, window_end: date) -> dict:
        return {"shortCode": self.code, "commodity": "POWER", "pricing": "O", "area": self.area,
                "product": "Base", "maturity": self.maturity,
                "startDate": window_start.isoformat(), "endDate": window_end.isoformat()}

    @property
    def label(self) -> str:
        return f"EEX options {self.area} Base {self.name} ({self.code} / {self.maturity})"


def make_series(area: str, kind: str, start: date) -> OptionSeries:
    if area not in CODES:
        raise ValueError(f"marché {area!r} sans codes d'options vérifiés (marketdata.eex_options.CODES)")
    if start.day != 1:
        raise ValueError(f"la livraison commence le 1er du mois ({start})")
    if kind == "month":
        end, name = add_months(start, 1) - timedelta(days=1), f"M{start.month:02d}-{start.year % 100}"
    elif kind == "quarter":
        if start.month not in (1, 4, 7, 10):
            raise ValueError(f"début de trimestre invalide : {start}")
        end, name = add_months(start, 3) - timedelta(days=1), f"Q{(start.month - 1) // 3 + 1}-{start.year % 100}"
    elif kind == "year":
        if start.month != 1:
            raise ValueError(f"une année commence en janvier ({start})")
        end, name = date(start.year, 12, 31), f"Cal-{start.year % 100}"
    else:
        raise ValueError(f"type d'option inconnu : {kind!r}")
    return OptionSeries(area, kind, start, end, name, CODES[area][kind], f"{start:%Y%m}")


def candidates(area: str, session: date) -> list[OptionSeries]:
    """Séries négociables à une séance : livraison postérieure au mois de la séance."""
    m0 = date(session.year, session.month, 1)
    q0 = date(session.year, 3 * ((session.month - 1) // 3) + 1, 1)
    out = [make_series(area, "month", add_months(m0, i)) for i in range(1, N_MONTHS + 1)]
    out += [make_series(area, "quarter", add_months(q0, 3 * i)) for i in range(1, N_QUARTERS + 1)]
    out += [make_series(area, "year", date(session.year + i, 1, 1)) for i in range(1, N_YEARS + 1)]
    return out


def parse_options(payload: dict, series: OptionSeries) -> pd.DataFrame:
    """Réponse table-data-option → une ligne par (séance, strike). Lignes sans strike ni settlement ignorées."""
    if not isinstance(payload, dict) or "header" not in payload or "data" not in payload:
        raise DataNotFoundError(f"{series.label} : réponse au format inattendu")
    missing = [c for c in REQUIRED if c not in payload["header"]]
    if missing:
        raise DataNotFoundError(f"{series.label} : colonnes absentes {missing}")
    df = pd.DataFrame(payload["data"], columns=payload["header"])
    cols = ["session", "strike", "call", "put", *OPTIONAL.values()]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df[df["strikePrice"].notna() & (df["callSettlPx"].notna() | df["putSettlPx"].notna())]
    if df.empty:
        return pd.DataFrame(columns=cols)
    other = df[(df["shortCode"] != series.code) | (df["maturityDate"].astype(int) != int(series.maturity))]
    if len(other):
        raise DataNotFoundError(f"{series.label} : la réponse contient des lignes d'une autre série")
    out = pd.DataFrame({
        "session": [date.fromisoformat(str(x)[:10]) for x in df["tradeDate"]],
        "strike": pd.to_numeric(df["strikePrice"], errors="raise").astype(float).to_numpy(),
        "call": pd.to_numeric(df["callSettlPx"], errors="raise").astype(float).to_numpy(),
        "put": pd.to_numeric(df["putSettlPx"], errors="raise").astype(float).to_numpy()})
    for src, dst in OPTIONAL.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce").astype(float).to_numpy() if src in df else float("nan")
    if out.duplicated(["session", "strike"]).any():
        raise DataNotFoundError(f"{series.label} : plusieurs lignes pour une même séance et un même strike")
    return out.sort_values(["session", "strike"]).reset_index(drop=True)


def fetch_window(series_list: list[OptionSeries], window_start: date, window_end: date,
                 http: HttpGet = request_options, pause: float = 1.0) -> pd.DataFrame:
    """Toutes les séances de la fenêtre pour chaque série (une requête par série)."""
    parts = []
    for i, s in enumerate(series_list, 1):
        df = parse_options(http(s.params(window_start, window_end)), s)
        if len(df):
            parts.append(df.assign(kind=s.kind, name=s.name, code=s.code, maturity=s.maturity,
                                   start=s.start, end=s.end))
        if pause and i < len(series_list):
            time.sleep(pause)
    log.info("EEX options : %d série(s) interrogée(s), %d avec des settlements", len(series_list), len(parts))
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    order = ["session", "kind", "name", "code", "maturity", "start", "end", "strike", "call", "put",
             *OPTIONAL.values()]
    return df[order].sort_values(["session", "kind", "start", "strike"], key=lambda c: c.map(KIND_ORDER)
                                 if c.name == "kind" else c).reset_index(drop=True)
