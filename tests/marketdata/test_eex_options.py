"""Options EEX : format réel de la réponse table-data-option (relevé le 27/10/2026)."""
from datetime import date

import pytest

from marketdata import DataNotFoundError
from marketdata.eex_options import candidates, fetch_window, make_series, parse_options

HEADER = ["shortCode", "strikePrice", "maturityDate", "tradeDate", "callTotVolTrdd", "callGrossOpenInt",
          "callGrossOpenIntSz", "callNetOpenInt", "callNetOpenIntSz", "callSettlPx", "putTotVolTrdd",
          "putGrossOpenInt", "putGrossOpenIntSz", "putNetOpenInt", "putNetOpenIntSz", "putSettlPx",
          "callAggTotVolTrdd", "callAggGrossOpenInt", "callAggGrossOpenIntSz", "callAggNetOpenInt",
          "callAggNetOpenIntSz", "putAggTotVolTrdd", "putAggGrossOpenInt", "putAggGrossOpenIntSz",
          "putAggNetOpenInt", "putAggNetOpenIntSz", "displayYear", "displaySeason", "displayQuarter",
          "displayMonth", "displayWeek", "displayDay"]


def row(code, strike, maturity, session, call, put):
    return [code, strike, int(maturity), session, None, 0, 0, None, None, call, None, 0, 0, None, None, put,
            *[None] * 10, int(maturity[:4]), None, None, int(maturity[4:]), None, None]


def payload(code, maturity, rows):
    return {"header": HEADER, "data": [row(code, *r[:1], maturity, *r[1:]) for r in rows], "uOM": "MWh",
            "currency": "EUR"}


def test_series_and_candidates():
    s = make_series("FR", "year", date(2027, 1, 1))
    assert (s.code, s.maturity, s.name, s.end) == ("O7FY", "202701", "Cal-27", date(2027, 12, 31))
    assert s.params(date(2026, 10, 20), date(2026, 10, 26)) == {
        "shortCode": "O7FY", "commodity": "POWER", "pricing": "O", "area": "FR", "product": "Base",
        "maturity": "202701", "startDate": "2026-10-20", "endDate": "2026-10-26"}
    assert make_series("FR", "month", date(2026, 12, 1)).code == "O7FM"
    assert make_series("FR", "quarter", date(2027, 1, 1)).name == "Q1-27"
    c = candidates("FR", date(2026, 10, 26))
    assert [x.name for x in c if x.kind == "month"][:2] == ["M11-26", "M12-26"] and len(c) == 9 + 6 + 4
    assert [x.name for x in c if x.kind == "year"] == ["Cal-27", "Cal-28", "Cal-29", "Cal-30"]


def test_parse_real_values_and_parity():
    s = make_series("FR", "year", date(2027, 1, 1))
    df = parse_options(payload("O7FY", "202701", [(42, "2026-10-26", 79.631, 0.281),
                                                  (120, "2026-10-26", 5.951, 4.601)]), s)
    assert list(df["strike"]) == [42.0, 120.0] and df["session"].iloc[0] == date(2026, 10, 26)
    f = df["call"] - df["put"] + df["strike"]                 # parité sans actualisation
    assert f.round(3).tolist() == [121.35, 121.35]
    assert df["call_open_interest"].tolist() == [0.0, 0.0] and df["call_volume"].isna().all()


def test_rows_without_strike_or_settlement_are_ignored_and_foreign_rows_refused():
    s = make_series("FR", "month", date(2026, 12, 1))
    p = payload("O7FM", "202612", [(None, "2026-10-26", None, None)])
    assert parse_options(p, s).empty
    with pytest.raises(DataNotFoundError):
        parse_options(payload("O7FY", "202701", [(42, "2026-10-26", 1.0, 2.0)]), s)
    with pytest.raises(DataNotFoundError):
        parse_options({"header": ["shortCode"], "data": []}, s)


def test_fetch_window_multi_sessions():
    calls = []

    def http(params):
        calls.append(params)
        if params["shortCode"] != "O7FY" or params["maturity"] != "202701":
            return {"header": HEADER, "data": []}
        return payload("O7FY", "202701", [(100, "2026-10-23", 23.0, 2.0), (100, "2026-10-26", 23.4, 2.07)])

    series = candidates("FR", date(2026, 10, 26))
    df = fetch_window(series, date(2026, 10, 20), date(2026, 10, 26), http, pause=0)
    assert len(calls) == len(series) and sorted(df["session"].unique()) == [date(2026, 10, 23), date(2026, 10, 26)]
    assert set(df["name"]) == {"Cal-27"} and {"start", "end", "kind", "code"} <= set(df.columns)
