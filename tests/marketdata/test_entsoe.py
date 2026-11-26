from datetime import date

import numpy as np
import pandas as pd
import pytest

from marketdata import DataNotFoundError
from marketdata.entsoe import fetch_day_ahead, load_day_ahead, load_day_ahead_hourly
from marketdata.grid import to_hourly

TZ = "Europe/Paris"


class FakeClient:
    """Imite entsoe-py : borne de fin incluse, pas imposé par `resolution`."""

    def __init__(self, drop=None):
        self.calls = 0
        self.drop = drop

    def query_day_ahead_prices(self, area, start, end, resolution="60min"):
        self.calls += 1
        freq = "15min" if resolution == "15min" else "h"
        idx = pd.date_range(start, end, freq=freq, inclusive="both").tz_convert("Europe/Brussels")
        s = pd.Series(np.arange(len(idx), dtype=float), index=idx)
        return s.drop(s.index[self.drop]) if self.drop is not None else s


def test_journee_15_min_borne_de_fin_retiree():
    s = fetch_day_ahead(FakeClient(), "FR", date(2026, 10, 4), date(2026, 10, 4), TZ)
    assert len(s) == 96


@pytest.mark.parametrize("day, n", [(date(2026, 10, 25), 100), (date(2026, 3, 29), 92)])
def test_changements_d_heure_15_min(day, n):
    assert len(fetch_day_ahead(FakeClient(), "FR", day, day, TZ)) == n


def test_changement_d_heure_horaire():
    assert len(fetch_day_ahead(FakeClient(), "FR", date(2024, 10, 27), date(2024, 10, 27), TZ)) == 25


def test_pas_manquant_refuse():
    with pytest.raises(DataNotFoundError):
        fetch_day_ahead(FakeClient(drop=10), "FR", date(2026, 10, 4), date(2026, 10, 4), TZ)


def test_bloc_a_cheval_sur_le_passage_au_15_min_refuse():
    with pytest.raises(ValueError):
        fetch_day_ahead(FakeClient(), "FR", date(2025, 9, 30), date(2025, 10, 1), TZ)


def test_agregation_horaire_par_moyenne():
    idx = pd.date_range("2026-10-04", periods=8, freq="15min", tz="UTC")
    h = to_hourly(pd.Series([1.0, 2.0, 3.0, 6.0, 0.0, 0.0, 0.0, 4.0], index=idx))
    assert list(h) == [3.0, 1.0]


def test_horaire_a_cheval_sur_le_passage_au_15_min():
    h = load_day_ahead_hourly(FakeClient(), "FR", date(2025, 9, 30), date(2025, 10, 1), TZ)
    assert len(h) == 48


def test_archive_mensuelle(tmp_path):
    pytest.importorskip("pyarrow")
    client = FakeClient()
    a = load_day_ahead(client, "FR", date(2026, 10, 1), date(2026, 10, 5), TZ, root=tmp_path, market="FR")
    assert client.calls == 1 and len(a) == 5 * 96
    assert (tmp_path / "entsoe/day_ahead/market=FR/month=2026-10/day_ahead.parquet").exists()
    b = load_day_ahead(client, "FR", date(2026, 10, 2), date(2026, 10, 3), TZ, root=tmp_path, market="FR")
    assert client.calls == 1 and len(b) == 2 * 96              # servi par le cache
    load_day_ahead(client, "FR", date(2026, 10, 1), date(2026, 10, 7), TZ, root=tmp_path, market="FR")
    assert client.calls == 2                                   # le mois s'allonge : rechargé


def test_une_requete_par_mois_manquant(tmp_path):
    pytest.importorskip("pyarrow")
    client = FakeClient()
    s = load_day_ahead(client, "FR", date(2024, 1, 1), date(2024, 3, 31), TZ, root=tmp_path, market="FR")
    assert client.calls == 3 and len(s) == (31 + 29 + 31) * 24 - 1
    months = sorted(p.name for p in (tmp_path / "entsoe/day_ahead/market=FR").iterdir())
    assert months == ["month=2024-01", "month=2024-02", "month=2024-03"]
    client2 = FakeClient()
    load_day_ahead(client2, "FR", date(2023, 12, 1), date(2024, 2, 29), TZ, root=tmp_path, market="FR")
    assert client2.calls == 1          # seul décembre 2023 manquait


def test_pas_manquants_completes_par_l_archive(tmp_path):
    pytest.importorskip("pyarrow")
    # Archive du mois complète, puis ENTSO-E « perd » la journée du 3 (96 pas) au téléchargement.
    first = load_day_ahead(FakeClient(), "FR", date(2026, 10, 1), date(2026, 10, 5), TZ,
                           root=tmp_path, market="FR")
    client = FakeClient(drop=slice(2 * 96, 3 * 96))
    s = load_day_ahead(client, "FR", date(2026, 10, 1), date(2026, 10, 6), TZ,
                       root=tmp_path, market="FR")
    assert client.calls == 1 and len(s) == 6 * 96
    day3 = s[(s.index >= pd.Timestamp("2026-10-03", tz=TZ)) & (s.index < pd.Timestamp("2026-10-04", tz=TZ))]
    assert (day3.values == first.loc[day3.index].values).all()   # valeurs archivées, rien d'inventé


def test_pas_manquants_sans_archive_refuses(tmp_path):
    pytest.importorskip("pyarrow")
    with pytest.raises(DataNotFoundError):
        load_day_ahead(FakeClient(drop=slice(10, 20)), "FR", date(2026, 10, 1), date(2026, 10, 5), TZ,
                       root=tmp_path, market="FR")


def test_pas_manquants_absents_aussi_de_l_archive_refuses(tmp_path):
    pytest.importorskip("pyarrow")
    load_day_ahead(FakeClient(), "FR", date(2026, 10, 1), date(2026, 10, 3), TZ, root=tmp_path, market="FR")
    with pytest.raises(DataNotFoundError):
        load_day_ahead(FakeClient(drop=slice(4 * 96, 5 * 96)), "FR", date(2026, 10, 1), date(2026, 10, 6),
                       TZ, root=tmp_path, market="FR")


def test_jour_non_publie_tolere_dans_le_dernier_mois_seulement(tmp_path):
    pytest.importorskip("pyarrow")
    client = FakeClient(drop=slice(2 * 96, 3 * 96))
    s = load_day_ahead(client, "FR", date(2026, 10, 1), date(2026, 10, 6), TZ,
                       root=tmp_path, market="FR", tolerate_last_month_gaps=True)
    assert len(s) == 5 * 96                       # rien d'inventé
    with pytest.raises(DataNotFoundError):        # mois antérieur : toujours strict
        load_day_ahead(FakeClient(drop=slice(2 * 96, 3 * 96)), "FR", date(2026, 9, 1),
                       date(2026, 10, 6), TZ, root=tmp_path / "b", market="FR",
                       tolerate_last_month_gaps=True)


def test_horaire_ecarte_les_heures_incompletes_du_dernier_mois():
    h = load_day_ahead_hourly(FakeClient(drop=slice(2 * 96, 3 * 96)), "FR", date(2026, 10, 1),
                              date(2026, 10, 6), TZ, tolerate_last_month_gaps=True)
    assert len(h) == 5 * 24
    with pytest.raises(DataNotFoundError):
        load_day_ahead_hourly(FakeClient(drop=slice(2 * 96, 3 * 96)), "FR", date(2026, 10, 1),
                              date(2026, 10, 6), TZ)
