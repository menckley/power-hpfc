"""Configuration d'un marché (fichier YAML dans config/)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import holidays
import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@dataclass(frozen=True)
class PeakDefinition:
    start_hour: int
    end_hour: int
    weekdays: tuple[int, ...]
    includes_holidays: bool


@dataclass(frozen=True)
class HPFCConfig:
    market: str
    entsoe_area: str
    holiday_country: str
    tz: str
    unit: str
    peak: PeakDefinition
    history_start: date
    excluded_periods: tuple[tuple[date, date], ...]
    n_months: int
    n_quarters: int
    n_years: int
    short_days: int
    short_window_days: int
    cascade_days: int
    intra_start: date
    min_obs: int
    min_full_years: int
    arbitrage_tol: float
    level_tol: float
    max_iterations: int
    source_hash: str = ""          # empreinte du fichier YAML (traçabilité)

    @classmethod
    def from_yaml(cls, path: str | Path) -> HPFCConfig:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        cfg = cls(
            market=raw["market"], entsoe_area=raw["entsoe_area"],
            holiday_country=raw["holiday_country"], tz=raw["tz"], unit=raw["unit"],
            peak=PeakDefinition(raw["peak"]["start_hour"], raw["peak"]["end_hour"],
                                tuple(raw["peak"]["weekdays"]), bool(raw["peak"]["includes_holidays"])),
            history_start=raw["history"]["start"],
            excluded_periods=tuple((s, e) for s, e in raw["history"]["excluded_periods"]),
            n_months=raw["products"]["n_months"], n_quarters=raw["products"]["n_quarters"],
            n_years=raw["products"]["n_years"], short_days=raw["products"]["short_days"],
            short_window_days=raw["products"]["short_window_days"],
            cascade_days=int(raw["products"]["cascade_days"]),
            intra_start=raw["shape"]["intra_start"], min_obs=raw["shape"]["min_obs"],
            min_full_years=raw["shape"]["min_full_years"],
            arbitrage_tol=float(raw["calibration"]["arbitrage_tol"]),
            level_tol=float(raw["calibration"]["level_tol"]),
            max_iterations=int(raw["calibration"]["max_iterations"]),
            source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        errors = []
        p = self.peak
        if not 0 <= p.start_hour < p.end_hour <= 24:
            errors.append("heures de peak incohérentes")
        if not set(p.weekdays) <= set(range(7)):
            errors.append("jours de peak invalides")
        for name in ("history_start", "intra_start"):
            if not isinstance(getattr(self, name), date):
                errors.append(f"{name} doit être une date AAAA-MM-JJ")
        for s, e in self.excluded_periods:
            if not (isinstance(s, date) and isinstance(e, date)) or s > e:
                errors.append(f"période exclue invalide : {s} -> {e}")
        if isinstance(self.intra_start, date) and isinstance(self.history_start, date) \
                and self.intra_start < self.history_start:
            errors.append("intra_start antérieur au début de l'historique")
        if min(self.n_months, self.n_quarters, self.n_years, self.min_obs, self.min_full_years) < 1:
            errors.append("paramètres de comptage non positifs")
        if errors:
            raise ValueError("Configuration invalide :\n- " + "\n- ".join(errors))

    def holiday_calendar(self, years) -> holidays.HolidayBase:
        return holidays.country_holidays(self.holiday_country, years=list(years))

    def curve_start(self, pricing_date: date) -> date:
        """La courbe démarre le lendemain de la séance de pricing."""
        return pricing_date + timedelta(days=1)

    def curve_end(self, pricing_date: date) -> date:
        """Fin de la dernière année cotée."""
        return date(pricing_date.year + self.n_years, 12, 31)


def load_config(market: str) -> HPFCConfig:
    path = CONFIG_DIR / f"{market}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"configuration absente : {path}")
    return HPFCConfig.from_yaml(path)
