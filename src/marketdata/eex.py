"""Settlements des futures power EEX via l'API publique du site eex.com.

On reproduit la requête « table-data » que fait la page eex.com (aucun compte,
aucune clé). Ce n'est pas l'API payante DataSource. Limites : API non documentée
(elle peut changer sans préavis) et historique d'environ 45 jours seulement.

Règles vérifiées pour la France (notebook de recherche, cellules 4.3 à 4.6) :
- même requête pour tous les produits ; seuls shortCode et maturityType changent ;
- mois / trimestres / années : `maturity` = premier mois de livraison (AAAAMM) ;
- jours : code + numéro du jour dans le mois de `maturity` (F707 = 7 du mois) ;
- week-ends et semaines : rattachés au mois de leur DIMANCHE, numérotés par le rang
  de ce dimanche dans le mois (semaine du 28/09 au 04/10/2026 = F7B1 d'octobre) ;
- le settlement de la veille de livraison d'un contrat court est le prix spot réalisé ;
- un contrat expiré renvoie settlPx = null.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd
import requests

from marketdata.errors import DataNotFoundError

API_URL = "https://api.eex-group.com/pub/market-data/table-data"
HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.eex.com",
    "Referer": "https://www.eex.com/",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}
REQUIRED_COLS = ("shortCode", "maturityDate", "tradeDate", "settlPx")
MATURITY_TYPE = {"day": "Day", "weekend": "Weekend", "week": "Week",
                 "month": "Month", "quarter": "Quarter", "year": "Year"}
KIND_ORDER = {"day": 0, "weekend": 1, "week": 2, "month": 3, "quarter": 4, "year": 5}

# Codes produits vérifiés. Un marché absent de ce dictionnaire n'est pas utilisable :
# ses codes doivent d'abord être relevés sur eex.com et vérifiés.
MARKETS = {
    "FR": {
        "standard": {("Base", "month"): "F7BM", ("Base", "quarter"): "F7BQ", ("Base", "year"): "F7BY",
                     ("Peak", "month"): "F7PM", ("Peak", "quarter"): "F7PQ", ("Peak", "year"): "F7PY"},
        "day": {"Base": "F7", "Peak": "P7"},
        "weekend": {"Base": "F7W", "Peak": "P7W"},
        "week": {"Base": "F7B", "Peak": "F7P"},
    },
}

HttpGet = Callable[[dict], dict]
log = logging.getLogger("marketdata.eex")


@dataclass(frozen=True)
class Contract:
    area: str
    profile: str      # "Base" ou "Peak"
    kind: str         # day, weekend, week, month, quarter, year
    start: date       # premier jour de livraison
    end: date         # dernier jour de livraison (inclus)
    name: str
    code: str         # shortCode EEX
    maturity: str     # AAAAMM
    mtype: str        # maturityType EEX

    def params(self, window_start: date, window_end: date) -> dict:
        """Paramètres de la requête table-data pour des séances [window_start, window_end]."""
        return {
            "shortCode": self.code, "commodity": "POWER", "pricing": "F", "area": self.area,
            "product": self.profile, "maturity": self.maturity,
            "startDate": window_start.isoformat(), "endDate": window_end.isoformat(),
            "maturityType": self.mtype, "isRolling": "true",
        }

    @property
    def label(self) -> str:
        return f"EEX {self.area} {self.profile} {self.name} ({self.code} / {self.maturity})"


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def sunday_slot(sunday: date) -> tuple[str, int]:
    """Mois de rattachement (AAAAMM) et numéro d'une semaine ou d'un week-end EEX."""
    if sunday.weekday() != 6:
        raise ValueError(f"{sunday} n'est pas un dimanche")
    return f"{sunday:%Y%m}", (sunday.day - 1) // 7 + 1


def make_contract(area: str, profile: str, kind: str, start: date) -> Contract:
    """Construit un contrat EEX à partir de sa période de livraison."""
    if area not in MARKETS:
        raise ValueError(f"marché {area!r} non configuré dans marketdata.eex.MARKETS")
    if profile not in ("Base", "Peak"):
        raise ValueError(f"profil inconnu : {profile!r}")
    codes = MARKETS[area]

    if kind == "day":
        if profile == "Peak" and start.weekday() >= 5:
            raise ValueError(f"pas de contrat Day peak le week-end ({start})")
        end, name = start, f"D {start:%d/%m/%y}"
        code, maturity = f"{codes['day'][profile]}{start.day:02d}", f"{start:%Y%m}"
    elif kind == "weekend":
        if start.weekday() != 5:
            raise ValueError(f"un week-end commence un samedi ({start})")
        end = start + timedelta(days=1)
        maturity, n = sunday_slot(end)
        name, code = f"WE {start:%d/%m}-{end:%d/%m/%y}", f"{codes['weekend'][profile]}{n}"
    elif kind == "week":
        if start.weekday() != 0:
            raise ValueError(f"une semaine commence un lundi ({start})")
        end = start + timedelta(days=6)
        maturity, n = sunday_slot(end)
        name, code = f"W {start:%d/%m}-{end:%d/%m/%y}", f"{codes['week'][profile]}{n}"
    elif kind in ("month", "quarter", "year"):
        if start.day != 1:
            raise ValueError(f"{kind} : la livraison commence le 1er du mois ({start})")
        if kind == "month":
            end, name = add_months(start, 1) - timedelta(days=1), f"M{start.month:02d}-{start.year % 100}"
        elif kind == "quarter":
            if start.month not in (1, 4, 7, 10):
                raise ValueError(f"début de trimestre invalide : {start}")
            end, name = add_months(start, 3) - timedelta(days=1), f"Q{(start.month - 1) // 3 + 1}-{start.year % 100}"
        else:
            if start.month != 1:
                raise ValueError(f"une année commence en janvier ({start})")
            end, name = date(start.year, 12, 31), f"Cal-{start.year % 100}"
        code, maturity = codes["standard"][(profile, kind)], f"{start:%Y%m}"
    else:
        raise ValueError(f"type de contrat inconnu : {kind!r}")

    return Contract(area, profile, kind, start, end, name, code, maturity, MATURITY_TYPE[kind])


RETRY_STATUS = {429, 500, 502, 503, 504}


def _retry_wait(response, attempt: int, backoff: float) -> float:
    header = response.headers.get("Retry-After") if response is not None else None
    try:
        return max(float(header), 1.0)
    except (TypeError, ValueError):
        return backoff * 2 ** attempt


def request_table(params: dict, timeout: int = 30, max_retries: int = 6, backoff: float = 15.0,
                  sleep: Callable[[float], None] = time.sleep, url: str = API_URL) -> dict:
    """Appel brut de l'API table-data : JSON tel quel, ou erreur explicite.

    Limite de débit (HTTP 429) ou erreur serveur (5xx) : nouvel essai après une attente
    croissante (15 s, 30 s, 60 s...), ou celle indiquée par EEX (en-tête Retry-After).
    """
    label = f"EEX {params.get('shortCode')} {params.get('maturity')}"
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise DataNotFoundError(f"{label} : requête impossible : {exc!r}") from exc
            wait = backoff * 2 ** attempt
            log.warning("%s : erreur réseau, nouvel essai dans %.0f s", label, wait)
            sleep(wait)
            continue
        if r.status_code in RETRY_STATUS and attempt < max_retries:
            wait = _retry_wait(r, attempt, backoff)
            log.warning("%s : HTTP %s, nouvel essai dans %.0f s", label, r.status_code, wait)
            sleep(wait)
            continue
        if not r.ok:
            raise DataNotFoundError(f"{label} : HTTP {r.status_code} : {r.text[:300]!r}")
        try:
            return r.json()
        except ValueError as exc:
            raise DataNotFoundError(f"{label} : réponse non JSON : {r.text[:300]!r}") from exc
    raise DataNotFoundError(f"{label} : échec après {max_retries} nouveaux essais")


def parse_history(payload: dict, contract: Contract) -> pd.DataFrame:
    """Réponse table-data -> DataFrame indexé par séance, colonne `settlement` (NaN si non publié)."""
    if not isinstance(payload, dict) or "header" not in payload or "data" not in payload:
        raise DataNotFoundError(f"{contract.label} : réponse au format inattendu")
    missing = [c for c in REQUIRED_COLS if c not in payload["header"]]
    if missing:
        raise DataNotFoundError(f"{contract.label} : colonnes absentes {missing}")
    df = pd.DataFrame(payload["data"], columns=payload["header"])
    if df.empty:
        return pd.DataFrame({"settlement": pd.Series(dtype=float)},
                            index=pd.Index([], name="trade_date"))
    other = df[(df["shortCode"] != contract.code) | (df["maturityDate"].astype(int) != int(contract.maturity))]
    if len(other):
        raise DataNotFoundError(f"{contract.label} : la réponse contient des lignes d'un autre contrat")
    df["trade_date"] = [date.fromisoformat(str(x)[:10]) for x in df["tradeDate"]]
    if df["trade_date"].duplicated().any():
        raise DataNotFoundError(f"{contract.label} : plusieurs lignes pour une même séance")
    out = pd.DataFrame({"settlement": pd.to_numeric(df["settlPx"], errors="raise").astype(float).to_numpy()},
                       index=pd.Index(df["trade_date"], name="trade_date"))
    return out.sort_index()


def fetch_history(contract: Contract, window_start: date, window_end: date,
                  http: HttpGet = request_table) -> pd.DataFrame:
    """Settlements d'un contrat sur les séances [window_start, window_end]."""
    return parse_history(http(contract.params(window_start, window_end)), contract)


def fetch_settlement(contract: Contract, session: date, http: HttpGet = request_table,
                     lookback_days: int = 7) -> float | None:
    """Settlement à une séance donnée ; None si EEX n'en publie pas pour ce contrat ce jour-là."""
    hist = fetch_history(contract, session - timedelta(days=lookback_days), session, http)
    if session not in hist.index or pd.isna(hist.at[session, "settlement"]):
        return None
    return float(hist.at[session, "settlement"])


def listed_candidates(area: str, session: date, n_months: int, n_quarters: int, n_years: int,
                      short_days: int = 11, short_window_days: int = 35,
                      peak_weekend: bool = False) -> list[Contract]:
    """Contrats à interroger pour une séance : livraison strictement postérieure à la séance.

    Standards : les n prochains mois, trimestres et années. Courts : les jours des
    `short_days` prochains jours, les week-ends et semaines commençant dans les
    `short_window_days` prochains jours. Les week-ends peak (samedi-dimanche 8 h-20 h)
    sont exclus par défaut : ils ne correspondent pas à la définition usuelle du peak.
    """
    out: list[Contract] = []
    m0 = date(session.year, session.month, 1)
    q0 = date(session.year, 3 * ((session.month - 1) // 3) + 1, 1)
    for profile in ("Base", "Peak"):
        for i in range(1, n_months + 1):
            out.append(make_contract(area, profile, "month", add_months(m0, i)))
        for i in range(1, n_quarters + 1):
            out.append(make_contract(area, profile, "quarter", add_months(q0, 3 * i)))
        for i in range(1, n_years + 1):
            out.append(make_contract(area, profile, "year", date(session.year + i, 1, 1)))
        for i in range(1, short_days + 1):
            d = session + timedelta(days=i)
            if profile == "Base" or d.weekday() < 5:
                out.append(make_contract(area, profile, "day", d))
        for i in range(1, short_window_days + 1):
            d = session + timedelta(days=i)
            if d.weekday() == 5 and (profile == "Base" or peak_weekend):
                out.append(make_contract(area, profile, "weekend", d))
            if d.weekday() == 0:
                out.append(make_contract(area, profile, "week", d))
    return sorted(out, key=lambda c: (c.profile, KIND_ORDER[c.kind], c.start))


def settlement_at(hist: pd.DataFrame, session: date) -> float:
    """Settlement d'un historique à une séance ; NaN si absent ou non publié."""
    if session in hist.index and not pd.isna(hist.at[session, "settlement"]):
        return float(hist.at[session, "settlement"])
    return float("nan")


def fetch_sessions(contracts_by_session: dict[date, list[Contract]], http: HttpGet = request_table,
                   pause: float = 1.0, lookback_days: int = 7) -> dict[date, pd.DataFrame]:
    """Settlements de plusieurs séances en interrogeant chaque contrat UNE seule fois,
    sur une fenêtre couvrant toutes les séances (beaucoup moins de requêtes qu'une
    séance après l'autre)."""
    if not contracts_by_session:
        return {}
    unique = sorted({c for cs in contracts_by_session.values() for c in cs},
                    key=lambda c: (c.profile, KIND_ORDER[c.kind], c.start))
    w0 = min(contracts_by_session) - timedelta(days=lookback_days)
    w1 = max(contracts_by_session)
    histories = {}
    for i, c in enumerate(unique, 1):
        histories[c] = fetch_history(c, w0, w1, http)
        if i % 20 == 0 or i == len(unique):
            log.info("EEX : %d / %d contrats interrogés", i, len(unique))
        if pause:
            time.sleep(pause)
    return {s: pd.DataFrame([{**asdict(c), "settlement": settlement_at(histories[c], s)} for c in cs])
            for s, cs in contracts_by_session.items()}


def fetch_session(contracts: list[Contract], session: date, http: HttpGet = request_table,
                  pause: float = 1.0) -> pd.DataFrame:
    """Settlement de chaque contrat à la séance donnée (NaN si non publié ce jour-là)."""
    return fetch_sessions({session: contracts}, http, pause)[session]


def latest_session(area: str, today: date, http: HttpGet = request_table,
                   lookback_days: int = 10) -> date | None:
    """Dernière séance publiée, repérée sur le mois base le plus proche (toujours coté)."""
    probe = make_contract(area, "Base", "month", add_months(date(today.year, today.month, 1), 1))
    hist = fetch_history(probe, today - timedelta(days=lookback_days), today, http).dropna()
    return max(hist.index) if len(hist) else None
