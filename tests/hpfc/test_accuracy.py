"""Précision de la HPFC face au spot réalisé, sur une archive synthétique (aucun accès réseau)."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from hpfc import accuracy, archive, cli
from hpfc.config import load_config
from marketdata import DataNotFoundError
from marketdata.store import partition_dir, write_parquet

TZ = "Europe/Paris"


def _utc(start: date, end: date, freq: str) -> pd.DatetimeIndex:
    """Pas locaux de [start, end] (jours inclus), en UTC."""
    loc = pd.date_range(pd.Timestamp(start, tz=TZ), pd.Timestamp(end + timedelta(days=1), tz=TZ),
                        freq=freq, inclusive="left")
    return loc.tz_convert("UTC")


def _true_price(idx: pd.DatetimeIndex) -> np.ndarray:
    loc = idx.tz_convert(TZ)
    return 70 + 25 * np.sin(np.pi * loc.hour.to_numpy() / 24) + loc.day.to_numpy()


def _peak(idx: pd.DatetimeIndex) -> np.ndarray:
    loc = idx.tz_convert(TZ)
    return (loc.weekday < 5) & (loc.hour >= 8) & (loc.hour < 20)


def _write_spot(root, last_full: date, partial_hours: int = 10):
    """Spot au quart d'heure (format de l'archive depuis le 01/10/2025), constant dans chaque heure :
    septembre et octobre 2026 jusqu'à `last_full`, puis `partial_hours` heures du jour suivant."""
    q = _utc(date(2026, 9, 1), last_full + timedelta(days=1), "15min")
    cutoff = pd.Timestamp(f"{last_full + timedelta(days=1)} {partial_hours:02d}:00", tz=TZ)   # sans calcul de durée
    q = q[q < cutoff]
    s = pd.Series(_true_price(q.floor("h")), index=q, name="price")
    for month in ("2026-09", "2026-10"):
        part = s[s.index.tz_convert(TZ).strftime("%Y-%m") == month]
        write_parquet(part, partition_dir(root, "entsoe/day_ahead", market="FR", month=month) / "day_ahead.parquet")


def _write_curve(root, d: date, offset: float, days: int = 40):
    idx = _utc(d + timedelta(days=1), d + timedelta(days=days), "h")
    curve = pd.DataFrame({"local": idx.tz_convert(TZ), "price": _true_price(idx) + offset,
                          "is_peak": _peak(idx)}, index=idx)
    archive.save_curve(curve, {"method": "test"}, "FR", d, root)


@pytest.fixture
def arch(tmp_path):
    _write_spot(tmp_path, date(2026, 10, 27))
    _write_curve(tmp_path, date(2026, 10, 1), offset=2.0)      # HPFC trop haute de 2 €/MWh
    _write_curve(tmp_path, date(2026, 10, 20), offset=-1.0)    # HPFC trop basse de 1 €/MWh
    _write_curve(tmp_path, date(2026, 10, 27), offset=0.0)     # rien de livré : ignorée
    return tmp_path


def test_dernier_jour_complet(arch):
    assert accuracy.last_spot_day("FR", TZ, arch) == date(2026, 10, 27)


def test_erreurs_journalieres(arch):
    df = accuracy.daily_errors(load_config("FR"), arch)
    c1 = df[df["curve_date"] == date(2026, 10, 1)]
    c2 = df[df["curve_date"] == date(2026, 10, 20)]
    assert len(c1) == 26 and len(c2) == 7                      # 02/10 -> 27/10 et 21/10 -> 27/10
    assert date(2026, 10, 27) not in set(df["curve_date"])
    assert df["delivery_date"].max() == date(2026, 10, 27)     # le 28/10, incomplet, n'est pas compté
    assert np.allclose(c1["base_error"], 2.0) and np.allclose(c2["base_error"], -1.0)
    assert np.allclose(c1["hourly_mae"], 2.0) and np.allclose(c1["hourly_rmse"], 2.0)
    assert np.allclose(df["profile_corr"], 1.0)
    assert list(c1["horizon_days"]) == list(range(1, 27))
    dst = df[df["delivery_date"] == date(2026, 10, 25)]        # passage à l'heure d'hiver : 25 heures
    assert len(dst) == 2 and np.allclose(dst["base_spot"],
                                         _true_price(_utc(date(2026, 10, 25), date(2026, 10, 25), "h")).mean())
    wk = c1[c1["delivery_date"] == date(2026, 10, 3)]          # samedi : aucune heure Peak
    assert wk["peak_hpfc"].isna().all()


def test_resume_par_horizon(arch):
    df = accuracy.daily_errors(load_config("FR"), arch)
    s = accuracy.summary(df)
    assert list(s.index) == [b[2] for b in accuracy.BUCKETS]
    assert s.loc["D+1", "observations"] == 2
    assert s.loc["D+1", "base_bias"] == pytest.approx(0.5)     # (2 - 1) / 2
    assert s.loc["D+1", "base_mae"] == pytest.approx(1.5)
    assert s.loc["D+2 to D+7", "observations"] == 12
    assert s.loc["D+8 to D+30", "observations"] == 19
    assert s.loc["D+31 to D+90", "observations"] == 0
    recent = accuracy.summary(df, since=date(2026, 10, 21))
    assert recent.loc["D+1", "observations"] == 1


def test_ligne_de_commande_et_relecture(arch, capsys):
    assert cli.main(["accuracy", "--root", str(arch)]) == 0
    assert "Précision HPFC FR" in capsys.readouterr().out
    df, s = accuracy.load("FR", arch)
    assert len(df) == 33 and s.loc["D+1", "observations"] == 2
    assert (accuracy.out_dir("FR", arch) / "summary.csv").exists()


def test_rien_a_comparer(tmp_path, capsys):
    _write_spot(tmp_path, date(2026, 10, 27))
    _write_curve(tmp_path, date(2026, 10, 27), offset=0.0)
    with pytest.raises(DataNotFoundError):
        accuracy.run(load_config("FR"), tmp_path)
    assert cli.main(["accuracy", "--root", str(tmp_path)]) == 0    # pas une erreur pour le workflow
    assert "non calculée" in capsys.readouterr().out
    assert not (accuracy.out_dir("FR", tmp_path) / "daily.parquet").exists()
