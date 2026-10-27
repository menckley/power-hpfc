"""Archivage des settlements d'options EEX (faux EEX, archive temporaire)."""
from datetime import date

import pandas as pd
import pytest

from hpfc import archive, cli, options
from marketdata import DataNotFoundError
from tests.marketdata.test_eex_options import HEADER, payload


def fake_http(sessions, fail=False):
    def http(params):
        if fail and params["shortCode"] == "O7FQ":
            raise DataNotFoundError("EEX indisponible")
        if (params["shortCode"], params["maturity"]) == ("O7FY", "202701"):
            return payload("O7FY", "202701", [(k, s, 121.35 - k + 2.0, 2.0) for s in sessions for k in (100, 110)])
        if (params["shortCode"], params["maturity"]) == ("O7FM", "202612"):
            return payload("O7FM", "202612", [(k, s, 130.0 - k + 1.0, 1.0) for s in sessions for k in (120,)])
        return {"header": HEADER, "data": []}
    return http


def futures(root, sessions):
    for s in sessions:
        archive.save_settlements(pd.DataFrame({"name": ["Cal-27"], "settlement": [121.35]}), "FR", s, root)


def test_archive_up_to_latest_futures_session_then_idempotent(tmp_path):
    futures(tmp_path, [date(2026, 10, 22), date(2026, 10, 23)])
    sessions = ["2026-10-22", "2026-10-23", "2026-10-26"]
    rep = options.collect("FR", today=date(2026, 10, 26), http=fake_http(sessions), root=tmp_path, pause=0)
    st = dict(zip(rep["session"], rep["status"]))
    assert st[date(2026, 10, 22)] == st[date(2026, 10, 23)] == "archived"
    assert st[date(2026, 10, 26)].startswith("waiting")      # futures du 26 pas encore archivés
    assert archive.list_option_sessions("FR", tmp_path) == [date(2026, 10, 22), date(2026, 10, 23)]
    df = archive.load_options("FR", session=date(2026, 10, 23), root=tmp_path)
    assert set(df["name"]) == {"Cal-27", "M12-26"} and len(df) == 3
    assert df["session"].iloc[0] == date(2026, 10, 23)        # colonne session relue telle quelle
    cal = archive.load_options("FR", name="Cal-27", root=tmp_path)
    assert len(cal) == 4 and (cal["call"] - cal["put"] + cal["strike"]).round(2).eq(121.35).all()
    futures(tmp_path, [date(2026, 10, 26)])
    rep2 = options.collect("FR", today=date(2026, 10, 26), http=fake_http(sessions), root=tmp_path, pause=0)
    st2 = dict(zip(rep2["session"], rep2["status"]))
    assert st2[date(2026, 10, 22)] == "already archived" and st2[date(2026, 10, 26)] == "archived"


def test_failure_writes_nothing(tmp_path):
    futures(tmp_path, [date(2026, 10, 23)])
    with pytest.raises(DataNotFoundError):
        options.collect("FR", today=date(2026, 10, 26), http=fake_http(["2026-10-23"], fail=True),
                        root=tmp_path, pause=0)
    assert archive.list_option_sessions("FR", tmp_path) == []


def test_cli_options(tmp_path, monkeypatch, capsys):
    futures(tmp_path, [date(2026, 10, 23)])
    monkeypatch.setattr(options, "request_options", fake_http(["2026-10-23"]))
    real = options.collect
    monkeypatch.setattr(options, "collect", lambda *a, **k: real(*a, **{**k, "http": fake_http(["2026-10-23"]),
                                                                      "pause": 0, "today": date(2026, 10, 26)}))
    assert cli.main(["options", "--root", str(tmp_path)]) == 0
    assert "archived" in capsys.readouterr().out
