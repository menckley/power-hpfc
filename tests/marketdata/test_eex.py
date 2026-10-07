from datetime import date

import pandas as pd
import pytest

from marketdata import DataNotFoundError
from marketdata.eex import (fetch_settlement, latest_session, listed_candidates, make_contract,
                            parse_history, sunday_slot)

HEADER = ["shortCode", "maturityDate", "tradeDate", "totVolTrdd", "grossOpenInt",
          "grossOpenIntSz", "netOpenInt", "netOpenIntSz", "settlPx"]


def payload(code, maturity, rows):
    """rows : liste de (tradeDate, settlPx), au format réel de l'API table-data."""
    return {"header": HEADER, "data": [[code, int(maturity), d, None, 0, 0, None, None, px] for d, px in rows],
            "uOM": "MWh", "currency": "EUR"}


def test_sunday_slot_regles_verifiees():
    assert sunday_slot(date(2026, 10, 4)) == ("202610", 1)    # semaine 28/09-04/10 = F7B1 d'octobre
    assert sunday_slot(date(2026, 10, 11)) == ("202610", 2)
    assert sunday_slot(date(2026, 11, 1)) == ("202611", 1)    # semaine 26/10-01/11 = F7B1 de novembre
    with pytest.raises(ValueError):
        sunday_slot(date(2026, 10, 5))


@pytest.mark.parametrize("profile, kind, start, code, maturity, mtype, end", [
    ("Base", "day", date(2026, 10, 7), "F707", "202610", "Day", date(2026, 10, 7)),
    ("Peak", "day", date(2026, 10, 7), "P707", "202610", "Day", date(2026, 10, 7)),
    ("Base", "weekend", date(2026, 10, 10), "F7W2", "202610", "Weekend", date(2026, 10, 11)),
    ("Base", "weekend", date(2026, 10, 31), "F7W1", "202611", "Weekend", date(2026, 11, 1)),
    ("Base", "week", date(2026, 10, 5), "F7B2", "202610", "Week", date(2026, 10, 11)),
    ("Base", "week", date(2026, 10, 26), "F7B1", "202611", "Week", date(2026, 11, 1)),
    ("Peak", "week", date(2026, 10, 26), "F7P1", "202611", "Week", date(2026, 11, 1)),
    ("Base", "month", date(2026, 11, 1), "F7BM", "202611", "Month", date(2026, 11, 30)),
    ("Peak", "quarter", date(2027, 1, 1), "F7PQ", "202701", "Quarter", date(2027, 3, 31)),
    ("Base", "year", date(2027, 1, 1), "F7BY", "202701", "Year", date(2027, 12, 31)),
])
def test_codes_contrats_france(profile, kind, start, code, maturity, mtype, end):
    c = make_contract("FR", profile, kind, start)
    assert (c.code, c.maturity, c.mtype, c.end) == (code, maturity, mtype, end)


def test_contrats_invalides():
    with pytest.raises(ValueError):
        make_contract("FR", "Peak", "day", date(2026, 10, 10))      # samedi
    with pytest.raises(ValueError):
        make_contract("FR", "Base", "week", date(2026, 10, 6))      # pas un lundi
    with pytest.raises(ValueError):
        make_contract("DE", "Base", "month", date(2026, 11, 1))     # marché non configuré


def test_liste_des_contrats_au_5_octobre_2026():
    contracts = listed_candidates("FR", date(2026, 10, 5), n_months=6, n_quarters=7, n_years=3)
    counts = pd.Series([(c.profile, c.kind) for c in contracts]).value_counts()
    expected = {("Base", "day"): 11, ("Base", "weekend"): 5, ("Base", "week"): 5, ("Base", "month"): 6,
                ("Base", "quarter"): 7, ("Base", "year"): 3, ("Peak", "day"): 9, ("Peak", "week"): 5,
                ("Peak", "month"): 6, ("Peak", "quarter"): 7, ("Peak", "year"): 3}
    assert counts.to_dict() == expected
    assert len(contracts) == 67
    assert all(c.start > date(2026, 10, 5) for c in contracts)


def test_lecture_reponse():
    c = make_contract("FR", "Peak", "month", date(2026, 11, 1))
    df = parse_history(payload("F7PM", "202611", [("2026-10-02", 200), ("2026-10-05", 195.25),
                                                   ("2026-10-01", None)]), c)
    assert list(df.index) == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]
    assert df.at[date(2026, 10, 2), "settlement"] == 200.0      # entier converti en décimal
    assert pd.isna(df.at[date(2026, 10, 1), "settlement"])


def test_reponse_d_un_autre_contrat_refusee():
    c = make_contract("FR", "Base", "month", date(2026, 11, 1))
    with pytest.raises(DataNotFoundError):
        parse_history(payload("F7BM", "202612", [("2026-10-05", 150.0)]), c)


def test_seance_en_double_refusee():
    c = make_contract("FR", "Base", "month", date(2026, 11, 1))
    with pytest.raises(DataNotFoundError):
        parse_history(payload("F7BM", "202611", [("2026-10-05", 1.0), ("2026-10-05", 2.0)]), c)


def test_format_inattendu_refuse():
    c = make_contract("FR", "Base", "month", date(2026, 11, 1))
    with pytest.raises(DataNotFoundError):
        parse_history({"data": []}, c)


def test_settlement_a_une_seance():
    c = make_contract("FR", "Base", "day", date(2026, 10, 7))
    http = lambda params: payload("F707", "202610", [("2026-10-02", 211.26), ("2026-10-05", 191.79)])
    assert fetch_settlement(c, date(2026, 10, 5), http) == 191.79
    assert fetch_settlement(c, date(2026, 10, 6), http) is None          # pas de ligne ce jour-là
    expired = lambda params: payload("F707", "202610", [("2026-10-05", None)])
    assert fetch_settlement(c, date(2026, 10, 5), expired) is None       # contrat expiré


def test_derniere_seance_publiee():
    http = lambda params: payload(params["shortCode"], params["maturity"],
                                  [("2026-10-01", 160.0), ("2026-10-02", 160.5), ("2026-10-05", 154.2),
                                   ("2026-10-06", None)])
    assert latest_session("FR", date(2026, 10, 7), http) == date(2026, 10, 5)
