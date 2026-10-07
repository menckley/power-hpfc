"""Run complet : séance EEX -> settlements -> spot -> forme -> courbe -> archive."""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from hpfc import archive
from hpfc.calendar import build_calendar, day_hours, validate_calendar
from hpfc.calibration import build_curve, build_system
from hpfc.config import HPFCConfig, load_config
from hpfc.constraints import check_coverage, independent_constraints
from hpfc.shape import fit_shape
from hpfc.validation import check_no_arbitrage, monthly_summary
from marketdata import DataNotFoundError
from marketdata import eex
from marketdata.entsoe import load_day_ahead_hourly, make_client

METHOD = "E"
log = logging.getLogger("hpfc.pipeline")
STANDARD_KINDS = ("month", "quarter", "year")


@dataclass
class RunResult:
    status: str                    # "created" ou "exists"
    pricing_date: date
    path: Path | None = None
    metadata: dict = field(default_factory=dict)


def code_version() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parents[2]).stdout.strip()
    except Exception:
        return "unknown"


def collect_settlements(cfg: HPFCConfig, sessions: list[date], http=eex.request_table,
                        root: Path = archive.DATA_ROOT, pause: float = 1.0) -> dict:
    """Settlements de plusieurs séances : archive si disponible, sinon EEX (requêtes groupées).

    Renvoie {séance: DataFrame} ou {séance: DataNotFoundError} si un produit standard manque.
    Une séance n'est archivée que si tous ses produits standards sont cotés.
    """
    archived = set(archive.list_sessions(cfg.market, root))
    out = {s: archive.load_settlements(cfg.market, session=s, quoted_only=False, root=root)
           for s in sessions if s in archived}
    todo = {s: eex.listed_candidates(cfg.market, s, cfg.n_months, cfg.n_quarters, cfg.n_years,
                                     cfg.short_days, cfg.short_window_days)
            for s in sessions if s not in archived}
    if todo:
        log.info("EEX : %d séance(s) à télécharger", len(todo))
    for s, df in eex.fetch_sessions(todo, http, pause).items():
        # Produit standard absent : refusé, sauf cascade juste avant livraison (voir config)
        cascading = pd.to_datetime(df["start"]) <= pd.Timestamp(s + timedelta(days=cfg.cascade_days))
        missing = df[df["kind"].isin(STANDARD_KINDS) & df["settlement"].isna() & ~cascading]
        if len(missing):
            out[s] = DataNotFoundError(f"séance {s} : settlements standards absents "
                                       f"{list(missing['profile'] + ' ' + missing['name'])}")
            continue
        archive.save_settlements(df, cfg.market, s, root)
        out[s] = df.assign(session=s)
    return out


def run(market: str, pricing_date: date | None = None, *, today: date | None = None,
        client=None, http=eex.request_table, root: Path = archive.DATA_ROOT,
        force: bool = False, pause: float = 1.0) -> RunResult:
    cfg = load_config(market)
    today = today or date.today()

    # 1. Séance de pricing : dernière séance publiée, sauf date imposée
    session = pricing_date or eex.latest_session(cfg.market, today, http)
    if session is None:
        raise DataNotFoundError("EEX : aucune séance publiée sur les 10 derniers jours")
    if archive.curve_exists(cfg.market, session, root) and not force:
        return RunResult("exists", session, archive.curve_dir(cfg.market, session, root))

    # 2. Settlements (archivés dès qu'ils sont complets)
    log.info("Séance de pricing : %s", session)
    all_settle = collect_settlements(cfg, [session], http, root, pause)[session]
    if isinstance(all_settle, Exception):
        raise all_settle
    settle = all_settle[all_settle["settlement"].notna()].reset_index(drop=True)

    # 3. Calendrier et contraintes
    start, end = cfg.curve_start(session), cfg.curve_end(session)
    cal = build_calendar(cfg, start, end)
    validate_calendar(cal, cfg)
    dh = day_hours(cal)
    coverage = check_coverage(settle, start, end, cfg.peak.weekdays)
    cs = independent_constraints(settle, dh, start, end, cfg.arbitrage_tol)
    log.info("%d produits cotés, %d contraintes indépendantes", len(settle), len(cs.b_eq))

    # 4. Historique spot (archivé mois par mois) et forme
    if client is None:
        client = make_client(os.environ.get("ENTSOE_API_KEY", ""))
    spot = load_day_ahead_hourly(client, cfg.entsoe_area, cfg.history_start, session, cfg.tz,
                                 root=root, market=cfg.market)
    hist = build_calendar(cfg, cfg.history_start, session, "h").join(spot)
    log.info("Estimation de la forme")
    model = fit_shape(hist, cfg, session)

    # 5. Calibration et contrôles
    log.info("Calibration")
    system = build_system(cal, cs, dh)
    curve, calib_info = build_curve(model, system, cfg)
    gaps = check_no_arbitrage(curve, cal, cs.constraints, settle, cfg.arbitrage_tol)

    # 6. Archive
    out = pd.DataFrame({"local": cal["local"], "price": curve, "is_peak": cal["is_peak"]}, index=cal.index)
    summary = monthly_summary(curve, cal).round(4)
    metadata = {
        "market": cfg.market, "pricing_date": session.isoformat(), "method": METHOD,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_version": code_version(), "config_hash": cfg.source_hash, "unit": cfg.unit,
        "curve": {"start": start.isoformat(), "end": end.isoformat(), "steps": len(out)},
        "products": {"queried": len(all_settle), "quoted": len(settle),
                     "not_quoted": sorted(all_settle.loc[all_settle["settlement"].isna(), "name"]
                                          .astype(str) + " " + all_settle.loc[all_settle["settlement"].isna(), "profile"]),
                     "coverage_days": coverage},
        "constraints": {"retained": len(cs.b_eq), "dropped": len(cs.dropped),
                        "degrees_of_freedom": cs.degrees_of_freedom,
                        "max_dropped_gap": float(cs.dropped["gap"].abs().max()) if len(cs.dropped) else 0.0},
        "shape": model.info,
        "calibration": calib_info,
        "checks": gaps,
        "monthly": {str(k): v for k, v in summary.to_dict(orient="index").items()},
    }
    path = archive.save_curve(out, metadata, cfg.market, session, root)
    log.info("Courbe archivée : %s", path)
    return RunResult("created", session, path, metadata)


def available_sessions(market: str, today: date, lookback_days: int = 60, http=eex.request_table) -> list[date]:
    """Séances encore disponibles sur l'API publique (repérées sur le mois base le plus proche)."""
    probe_start = eex.add_months(date(today.year, today.month, 1), 1)
    probe = eex.make_contract(market, "Base", "month", probe_start)
    hist = eex.fetch_history(probe, today - timedelta(days=lookback_days), today, http).dropna()
    return sorted(hist.index)


def backfill(market: str, *, today: date | None = None, lookback_days: int = 60, client=None,
             http=eex.request_table, root: Path = archive.DATA_ROOT, pause: float = 1.0) -> pd.DataFrame:
    """Construit les courbes de toutes les séances encore disponibles et pas encore archivées.

    Les settlements de toutes les séances sont d'abord téléchargés ensemble (chaque contrat
    interrogé une seule fois). Une séance en échec n'arrête pas les autres : son motif
    est reporté dans le tableau.
    """
    cfg = load_config(market)
    today = today or date.today()
    sessions = available_sessions(market, today, lookback_days, http)
    todo = [s for s in sessions if not archive.curve_exists(market, s, root)]
    log.info("%d séances disponibles, %d sans courbe", len(sessions), len(todo))
    collected = collect_settlements(cfg, todo, http, root, pause)

    rows = [{"session": s, "status": "exists", "detail": ""} for s in sessions if s not in todo]
    for i, s in enumerate(todo, 1):
        log.info("Courbe %d / %d : séance %s", i, len(todo), s)
        if isinstance(collected.get(s), Exception):
            rows.append({"session": s, "status": "failed", "detail": str(collected[s])})
            continue
        try:
            res = run(market, s, today=today, client=client, http=http, root=root, pause=pause)
            rows.append({"session": s, "status": res.status, "detail": ""})
        except Exception as exc:                       # noqa: BLE001 - reporté, pas ignoré
            rows.append({"session": s, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
    return pd.DataFrame(rows).sort_values("session").reset_index(drop=True)
