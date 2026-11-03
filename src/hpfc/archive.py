"""Archive des données et des courbes (dossier data/ du dépôt), et fonctions de lecture.

data/
├── eex/settlements/market=FR/session=AAAA-MM-JJ/settlements.parquet
├── eex/options/market=FR/session=AAAA-MM-JJ/options.parquet
├── ecb/estr.csv                    (journal des €STR publiés par la BCE)
├── entsoe/day_ahead/market=FR/month=AAAA-MM/day_ahead.parquet
└── curves/market=FR/pricing_date=AAAA-MM-JJ/{curve.parquet, metadata.json}
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from marketdata import DataNotFoundError
from marketdata.entsoe import MTU_15MIN_START
from marketdata.grid import check_grid, expected_grid, to_hourly
from marketdata.store import partition_dir, read_parquet, write_parquet

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


# ---------------------------------------------------------------- settlements EEX
def settlements_path(market: str, session: date, root: Path = DATA_ROOT) -> Path:
    return partition_dir(root, "eex/settlements", market=market, session=f"{_d(session)}") / "settlements.parquet"


def save_settlements(df: pd.DataFrame, market: str, session: date, root: Path = DATA_ROOT) -> Path:
    return write_parquet(df.assign(session=_d(session)).reset_index(drop=True),
                         settlements_path(market, session, root))


def list_sessions(market: str, root: Path = DATA_ROOT) -> list[date]:
    base = partition_dir(root, "eex/settlements", market=market)
    return sorted(_d(p.name.split("=", 1)[1]) for p in base.glob("session=*") if (p / "settlements.parquet").exists())


def load_settlements(market: str, session=None, start=None, end=None, contract: str | None = None,
                     profile: str | None = None, quoted_only: bool = True, root: Path = DATA_ROOT) -> pd.DataFrame:
    """Settlements archivés : une séance (`session`) ou une plage de séances [start, end],
    éventuellement filtrés sur un contrat (ex. "Cal-27", "M11-26") et un profil ("Base"/"Peak")."""
    sessions = list_sessions(market, root)
    if session is not None:
        sessions = [s for s in sessions if s == _d(session)]
    if start is not None:
        sessions = [s for s in sessions if s >= _d(start)]
    if end is not None:
        sessions = [s for s in sessions if s <= _d(end)]
    if not sessions:
        raise DataNotFoundError(f"aucune séance archivée pour {market} sur la période demandée")
    df = pd.concat([read_parquet(settlements_path(market, s, root)) for s in sessions], ignore_index=True)
    for col in ("start", "end", "session"):
        df[col] = pd.to_datetime(df[col]).dt.date
    if contract is not None:
        df = df[df["name"] == contract]
    if profile is not None:
        df = df[df["profile"] == profile]
    if quoted_only:
        df = df[df["settlement"].notna()]
    return df.sort_values(["session", "profile", "start"]).reset_index(drop=True)


# ---------------------------------------------------------------- €STR (BCE)
def estr_path(root: Path = DATA_ROOT) -> Path:
    return Path(root) / "ecb" / "estr.csv"


def load_estr(root: Path = DATA_ROOT) -> pd.Series:
    """Journal des €STR archivés (index : date de publication, valeur en %)."""
    path = estr_path(root)
    if not path.exists():
        raise DataNotFoundError("aucun €STR archivé")
    df = pd.read_csv(path)
    return pd.Series(df["estr_pct"].astype(float).to_numpy(), index=[_d(x) for x in df["date"]],
                     name="estr_pct").sort_index()


def save_estr(new: pd.Series, root: Path = DATA_ROOT) -> int:
    """Ajoute au journal les dates encore absentes (les valeurs déjà archivées ne sont jamais réécrites).
    Erreur si la BCE publie une valeur différente pour une date déjà archivée. Renvoie le nombre de dates ajoutées."""
    try:
        old = load_estr(root)
    except DataNotFoundError:
        old = pd.Series(dtype=float, name="estr_pct")
    common = old.index.intersection(new.index)
    changed = [d for d in common if abs(old[d] - new[d]) > 1e-9]
    if changed:
        raise DataNotFoundError(f"€STR révisé par la BCE pour {changed[:3]} : vérifier avant de modifier l'archive")
    add = new[~new.index.isin(old.index)]
    if add.empty:
        return 0
    allv = pd.concat([old, add]).sort_index()
    path = estr_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    pd.DataFrame({"date": [str(d) for d in allv.index], "estr_pct": allv.to_numpy()}).to_csv(tmp, index=False)
    tmp.replace(path)
    return len(add)


# ---------------------------------------------------------------- options EEX
def options_path(market: str, session: date, root: Path = DATA_ROOT) -> Path:
    return partition_dir(root, "eex/options", market=market, session=f"{_d(session)}") / "options.parquet"


def save_options(df: pd.DataFrame, market: str, session: date, root: Path = DATA_ROOT) -> Path:
    return write_parquet(df.reset_index(drop=True), options_path(market, session, root))


def list_option_sessions(market: str, root: Path = DATA_ROOT) -> list[date]:
    base = partition_dir(root, "eex/options", market=market)
    return sorted(_d(p.name.split("=", 1)[1]) for p in base.glob("session=*") if (p / "options.parquet").exists())


def load_options(market: str, session=None, start=None, end=None, name: str | None = None,
                 root: Path = DATA_ROOT) -> pd.DataFrame:
    """Settlements d'options archivés : une séance ou une plage [start, end], éventuellement une série
    (ex. "Cal-27", "M01-27", "Q1-27"). Une ligne par (séance, série, strike) : call, put, volumes, open interest."""
    sessions = list_option_sessions(market, root)
    if session is not None:
        sessions = [s for s in sessions if s == _d(session)]
    if start is not None:
        sessions = [s for s in sessions if s >= _d(start)]
    if end is not None:
        sessions = [s for s in sessions if s <= _d(end)]
    if not sessions:
        raise DataNotFoundError(f"aucune séance d'options archivée pour {market} sur la période demandée")
    df = pd.concat([read_parquet(options_path(market, s, root)) for s in sessions], ignore_index=True)
    for col in ("start", "end", "session"):
        df[col] = pd.to_datetime(df[col]).dt.date
    if name is not None:
        df = df[df["name"] == name]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- courbes
def curve_dir(market: str, pricing_date: date, root: Path = DATA_ROOT) -> Path:
    return partition_dir(root, "curves", market=market, pricing_date=f"{_d(pricing_date)}")


def curve_exists(market: str, pricing_date: date, root: Path = DATA_ROOT) -> bool:
    d = curve_dir(market, pricing_date, root)
    return (d / "curve.parquet").exists() and (d / "metadata.json").exists()


def save_curve(curve: pd.DataFrame, metadata: dict, market: str, pricing_date: date,
               root: Path = DATA_ROOT) -> Path:
    d = curve_dir(market, pricing_date, root)
    write_parquet(curve, d / "curve.parquet")
    tmp = d / "metadata.json.tmp"
    tmp.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(d / "metadata.json")        # métadonnées écrites en dernier : la courbe est complète
    return d


def list_curves(market: str, root: Path = DATA_ROOT) -> list[date]:
    base = partition_dir(root, "curves", market=market)
    return sorted(_d(p.name.split("=", 1)[1]) for p in base.glob("pricing_date=*")
                  if curve_exists(market, _d(p.name.split("=", 1)[1]), root))


def load_curve(market: str, pricing_date, root: Path = DATA_ROOT) -> pd.DataFrame:
    """Courbe horaire archivée : index UTC, colonnes local, price, is_peak."""
    if not curve_exists(market, _d(pricing_date), root):
        raise DataNotFoundError(f"aucune courbe {market} archivée pour le {_d(pricing_date)}")
    return read_parquet(curve_dir(market, pricing_date, root) / "curve.parquet")


def load_curve_metadata(market: str, pricing_date, root: Path = DATA_ROOT) -> dict:
    path = curve_dir(market, pricing_date, root) / "metadata.json"
    if not path.exists():
        raise DataNotFoundError(f"aucune métadonnée {market} pour le {_d(pricing_date)}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- spot ENTSO-E (lecture seule)
def load_spot(market: str, start, end, tz: str, hourly: bool = True, root: Path = DATA_ROOT) -> pd.Series:
    """Spot day-ahead archivé sur [start, end] (jours locaux), sans appel à l'API.
    Erreur si la période n'est pas entièrement archivée."""
    start, end = _d(start), _d(end)
    base = partition_dir(root, "entsoe/day_ahead", market=market)
    parts, m = [], date(start.year, start.month, 1)
    while m <= end:
        path = base / f"month={m:%Y-%m}" / "day_ahead.parquet"
        if not path.exists():
            raise DataNotFoundError(f"spot {market} non archivé pour {m:%Y-%m}")
        parts.append(read_parquet(path)["price"])
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    s = pd.concat(parts).sort_index()
    t0 = pd.Timestamp(start, tz=tz).tz_convert("UTC")
    t1 = pd.Timestamp(end + timedelta(days=1), tz=tz).tz_convert("UTC")
    s = s[(s.index >= t0) & (s.index < t1)]
    switch_day = MTU_15MIN_START
    switch = pd.Timestamp(switch_day, tz=tz).tz_convert("UTC")
    regimes = [r for r in (s[s.index < switch], s[s.index >= switch]) if len(r)]
    if hourly:
        s = pd.concat([to_hourly(r) for r in regimes]).sort_index()
        check_grid(s, expected_grid(start, end, tz, "h"), f"spot archivé {market}")
    else:
        spans = [(start, min(end, switch_day - timedelta(days=1)), "h"), (max(start, switch_day), end, "15min")]
        expected = [expected_grid(a, b, tz, f) for a, b, f in spans if a <= b]
        check_grid(s, expected[0].append(expected[1:]) if len(expected) > 1 else expected[0],
                   f"spot archivé {market}")
    return s.rename("price")
