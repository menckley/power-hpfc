"""Journal des €STR : ajout des seules dates absentes, jamais de réécriture, révision refusée."""
from datetime import date

import pandas as pd
import pytest

from hpfc import archive, cli
from marketdata import DataNotFoundError, ecb


def s(values):
    return pd.Series([v for _, v in values], index=[d for d, _ in values], name="estr_pct")


def test_journal_append_only(tmp_path):
    with pytest.raises(DataNotFoundError):
        archive.load_estr(tmp_path)
    assert archive.save_estr(s([(date(2026, 10, 28), 1.92), (date(2026, 10, 29), 1.93)]), tmp_path) == 2
    assert archive.save_estr(s([(date(2026, 10, 29), 1.93), (date(2026, 10, 30), 1.94)]), tmp_path) == 1
    j = archive.load_estr(tmp_path)
    assert list(j.index) == [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)] and j.iloc[-1] == 1.94
    with pytest.raises(DataNotFoundError, match="révisé"):
        archive.save_estr(s([(date(2026, 10, 29), 2.0)]), tmp_path)
    assert archive.load_estr(tmp_path)[date(2026, 10, 29)] == 1.93


def test_cli_starts_after_last_archived_date(tmp_path, monkeypatch, capsys):
    archive.save_estr(s([(date(2026, 10, 28), 1.92)]), tmp_path)
    seen = []

    def fake(start, end, http=None):
        seen.append(start)
        return s([(date(2026, 10, 29), 1.93)])
    monkeypatch.setattr(ecb, "fetch_estr", fake)
    assert cli.main(["estr", "--root", str(tmp_path)]) == 0
    assert seen == [date(2026, 10, 29)] and "1 valeur(s) ajoutée(s)" in capsys.readouterr().out
