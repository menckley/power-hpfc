import pandas as pd
import pytest

from marketdata.store import partition_dir, read_parquet, write_parquet


def test_chemin_partitionne():
    p = partition_dir("data", "curves", market="FR", pricing_date="2026-10-05")
    assert p.as_posix() == "data/curves/market=FR/pricing_date=2026-10-05"


def test_ecriture_lecture(tmp_path):
    pytest.importorskip("pyarrow")
    idx = pd.date_range("2026-10-06", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({"price": [1.0, 2.0, 3.0]}, index=idx)
    path = write_parquet(df, tmp_path / "a/b/x.parquet")
    back = read_parquet(path)
    assert back.index.tz is not None and back["price"].tolist() == [1.0, 2.0, 3.0]
    assert not path.with_suffix(".parquet.tmp").exists()


def test_lecture_sans_partition_du_chemin(tmp_path):
    """Une colonne qui porte le nom d'un dossier « clé=valeur » du chemin (cas des settlements) se relit
    telle quelle."""
    pytest.importorskip("pyarrow")
    from datetime import date
    df = pd.DataFrame({"name": ["Cal-27"], "settlement": [80.5], "session": [date(2026, 10, 23)]})
    path = write_parquet(df, tmp_path / "market=FR" / "session=2026-10-23" / "settlements.parquet")
    back = read_parquet(path)
    assert list(back.columns) == ["name", "settlement", "session"]
    assert back["session"].iloc[0] == date(2026, 10, 23) and back["settlement"].iloc[0] == 80.5
