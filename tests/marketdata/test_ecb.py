"""€STR de la BCE : lecture du CSV, nouveaux essais, erreur explicite (aucun accès réseau)."""
from datetime import date

import pytest
import requests

from marketdata import DataNotFoundError, ecb

CSV = ("KEY,FREQ,TIME_PERIOD,OBS_VALUE\nEST.B.EU000A2X2A25.WT,B,2026-10-29,1.93\n"
       "EST.B.EU000A2X2A25.WT,B,2026-10-28,1.92\n")


class R:
    def __init__(self, code, text=""):
        self.status_code, self.text, self.ok = code, text, 200 <= code < 300


def _get(responses, calls):
    def get(url, params=None, timeout=None):
        calls.append(params)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return get


def test_parse_and_fetch():
    s = ecb.parse_estr(CSV)
    assert list(s.index) == [date(2026, 10, 28), date(2026, 10, 29)] and s.iloc[-1] == 1.93
    assert ecb.parse_estr("").empty
    seen = []
    out = ecb.fetch_estr(date(2026, 10, 28), date(2026, 10, 29), http=lambda k, p: seen.append(p) or CSV)
    assert seen == [{"startPeriod": "2026-10-28", "endPeriod": "2026-10-29"}] and len(out) == 2
    with pytest.raises(DataNotFoundError):
        ecb.parse_estr("A,B\n1,2\n")


def test_retry_then_success_and_persistent_outage():
    calls, old = [], requests.get
    try:
        requests.get = _get([R(504), requests.ConnectionError("x"), R(200, CSV)], calls)
        assert ecb.request_csv(ecb.ESTR_KEY, {}, sleep=lambda s: None) == CSV and len(calls) == 3
        calls.clear()
        requests.get = _get([R(504)] * 4, calls)
        with pytest.raises(DataNotFoundError, match="HTTP 504"):
            ecb.request_csv(ecb.ESTR_KEY, {}, sleep=lambda s: None)
        assert len(calls) == 4
        calls.clear()
        requests.get = _get([R(400)], calls)
        with pytest.raises(DataNotFoundError, match="HTTP 400"):
            ecb.request_csv(ecb.ESTR_KEY, {}, sleep=lambda s: None)
        assert len(calls) == 1
    finally:
        requests.get = old
