"""Pipeline complet avec des sources simulées (aucun appel réseau)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from hpfc import archive, cli, pipeline
from marketdata import DataNotFoundError
from tests.hpfc.helpers import PRICING, synthetic_settlements

HEADER = ["shortCode", "maturityDate", "tradeDate", "totVolTrdd", "grossOpenInt",
          "grossOpenIntSz", "netOpenInt", "netOpenIntSz", "settlPx"]


def fake_eex(missing_standard: bool = False):
    """Imite l'API table-data pour la séance du 5/10/2026."""
    table = {(r.code, r.maturity): r.settlement for r in synthetic_settlements().itertuples()}

    def http(params):
        key = (params["shortCode"], params["maturity"])
        px = table.get(key)                         # None : contrat non coté ce jour-là
        if missing_standard and key == ("F7BY", "202901"):
            px = None
        rows = [[key[0], int(key[1]), d, None, 0, 0, None, None, px]
                for d in ("2026-10-01", "2026-10-02", PRICING.isoformat())]
        return {"header": HEADER, "data": rows}
    return http


class FakeSpot:
    """Imite entsoe-py : prix déterministes, borne de fin incluse, pas imposé par `resolution`."""

    def query_day_ahead_prices(self, area, start, end, resolution="60min"):
        freq = "15min" if resolution == "15min" else "h"
        idx = pd.date_range(start, end, freq=freq, inclusive="both")
        loc = idx.tz_convert("Europe/Paris")
        price = (60 + 20 * np.cos(2 * np.pi * (loc.month - 1) / 12) + 15 * np.sin(np.pi * loc.hour / 24)
                 - 10 * (loc.weekday >= 5) + 10 * np.sin(idx.asi8 / 3.6e12 * 0.7))
        return pd.Series(np.asarray(price, dtype=float), index=idx.tz_convert("Europe/Brussels"))


def test_run_complet_puis_rien_de_nouveau(tmp_path):
    pytest.importorskip("pyarrow")
    res = pipeline.run("FR", today=date(2026, 10, 7), client=FakeSpot(), http=fake_eex(),
                       root=tmp_path, pause=0)
    assert res.status == "created" and res.pricing_date == PRICING
    m = res.metadata
    assert (m["products"]["queried"], m["products"]["quoted"]) == (67, 65)
    assert m["products"]["not_quoted"] == ["W 09/11-15/11/26 Base", "W 09/11-15/11/26 Peak"]
    assert m["constraints"]["retained"] == 58 and m["curve"]["steps"] == 28393
    assert m["checks"]["max_gap_constraints"] < 1e-6

    curve = archive.load_curve("FR", PRICING, tmp_path)
    assert len(curve) == 28393 and list(curve.columns) == ["local", "price", "is_peak"]
    assert archive.list_sessions("FR", tmp_path) == [PRICING]
    assert archive.load_curve_metadata("FR", PRICING, tmp_path)["method"] == "E"

    again = pipeline.run("FR", today=date(2026, 10, 7), client=FakeSpot(), http=fake_eex(),
                         root=tmp_path, pause=0)
    assert again.status == "exists"


def test_lecture_de_l_archive_et_ligne_de_commande(tmp_path):
    pytest.importorskip("pyarrow")
    pipeline.run("FR", PRICING, client=FakeSpot(), http=fake_eex(), root=tmp_path, pause=0)

    cal27 = archive.load_settlements("FR", contract="Cal-27", profile="Base", root=tmp_path)
    expected = synthetic_settlements().query("name == 'Cal-27' and profile == 'Base'")["settlement"].iloc[0]
    assert len(cal27) == 1 and cal27["settlement"].iloc[0] == expected

    spot = archive.load_spot("FR", date(2026, 10, 1), date(2026, 10, 5), "Europe/Paris", root=tmp_path)
    assert len(spot) == 120

    out = tmp_path / "courbe.csv"
    assert cli.main(["curve", "--date", "2026-10-05", "--out", str(out), "--root", str(tmp_path)]) == 0
    assert len(pd.read_csv(out)) == 28393


def test_seance_incomplete_rien_n_est_archive(tmp_path):
    pytest.importorskip("pyarrow")
    with pytest.raises(DataNotFoundError, match="standards absents"):
        pipeline.run("FR", PRICING, client=FakeSpot(), http=fake_eex(missing_standard=True),
                     root=tmp_path, pause=0)
    assert archive.list_sessions("FR", tmp_path) == []
    assert archive.list_curves("FR", tmp_path) == []


def test_backfill_groupe(tmp_path):
    pytest.importorskip("pyarrow")
    calls = []
    base_http = fake_eex()

    def http(params):
        calls.append(params["shortCode"])
        return base_http(params)
    report = pipeline.backfill("FR", today=date(2026, 10, 7), client=FakeSpot(), http=http,
                               root=tmp_path, pause=0)
    assert list(report["session"]) == [date(2026, 10, 1), date(2026, 10, 2), PRICING]
    assert report.set_index("session").at[PRICING, "status"] == "created"
    assert len(calls) < 3 * 67                 # chaque contrat interrogé une seule fois
    again = pipeline.backfill("FR", today=date(2026, 10, 7), client=FakeSpot(), http=http,
                              root=tmp_path, pause=0)
    assert again.set_index("session").at[PRICING, "status"] == "exists"


def test_cascade_d_un_trimestre_juste_avant_livraison(tmp_path):
    """Le 29/09/2026, Q4-26 (livraison au 1/10) n'a plus de settlement : EEX l'a cascadé en mois."""
    pytest.importorskip("pyarrow")
    from hpfc.config import load_config
    session = date(2026, 9, 29)

    def http(params, absent=("202610",)):
        px = None if (params["shortCode"] in ("F7BQ", "F7PQ") and params["maturity"] in absent) else 100.0
        return {"header": HEADER, "data": [[params["shortCode"], int(params["maturity"]), session.isoformat(),
                                             None, 0, 0, None, None, px]]}
    cfg = load_config("FR")
    out = pipeline.collect_settlements(cfg, [session], http, tmp_path, pause=0)[session]
    assert not isinstance(out, Exception)
    assert out.query("name == 'Q4-26'")["settlement"].isna().all()

    far = pipeline.collect_settlements(cfg, [session], lambda p: http(p, ("202610", "202701")),
                                       tmp_path / "b", pause=0)[session]
    assert isinstance(far, DataNotFoundError)          # Q1-27 absent : refusé
