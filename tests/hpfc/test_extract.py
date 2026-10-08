from datetime import date

import numpy as np
import pandas as pd
import pytest

from hpfc import archive
from hpfc.calendar import build_calendar
from hpfc.extract import extract, resolve_pricing
from marketdata import DataNotFoundError
from tests.hpfc.helpers import cfg

DATES = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]


def _archive(root, dates=DATES):
    c = cfg()
    for d in dates:
        cal = build_calendar(c, c.curve_start(d), c.curve_end(d))
        price = 100 + 20 * np.sin(np.arange(len(cal)) / 50) + d.day
        out = pd.DataFrame({"local": cal["local"], "price": price, "is_peak": cal["is_peak"]}, index=cal.index)
        archive.save_curve(out, {"pricing_date": str(d)}, "FR", d, root)


def test_selection_des_courbes(tmp_path):
    pytest.importorskip("pyarrow")
    _archive(tmp_path)
    assert resolve_pricing("FR", "latest", tmp_path) == [date(2026, 10, 5)]
    assert resolve_pricing("FR", (date(2026, 10, 2), date(2026, 10, 9)), tmp_path) == DATES[1:]
    with pytest.raises(DataNotFoundError, match="2026-10-03"):
        resolve_pricing("FR", "2026-10-03", tmp_path)


def test_agregations(tmp_path):
    pytest.importorskip("pyarrow")
    _archive(tmp_path)
    q, _, partial = extract("FR", cfg(), "2026-10-05", "quarter", "peak", start="2027-01-01",
                            end="2027-12-31", root=tmp_path)
    assert list(q["hours"]) == [768, 780, 792, 792] and q["complete"].all() and partial == []
    m, _, partial = extract("FR", cfg(), "latest", "month", root=tmp_path)
    assert len(m) == 39 and partial == [pd.Timestamp("2026-10-01")]      # octobre 2026 : du 6 au 31
    w, _, _ = extract("FR", cfg(), "2026-10-05", "week", end="2026-11-01", root=tmp_path)
    assert w.loc[pd.Timestamp("2026-10-19"), "hours"] == 169              # passage à l'heure d'hiver
    h, _, _ = extract("FR", cfg(), "2026-10-05", "hour", "offpeak", start="2026-10-07", end="2026-10-07",
                      root=tmp_path)
    assert len(h) == 12


def test_plusieurs_courbes_une_colonne_par_date(tmp_path):
    pytest.importorskip("pyarrow")
    _archive(tmp_path)
    wide, _, _ = extract("FR", cfg(), (date(2026, 10, 1), date(2026, 10, 5)), "year", root=tmp_path)
    assert list(wide.columns) == ["2026-10-01", "2026-10-02", "2026-10-05"] and len(wide) == 4
