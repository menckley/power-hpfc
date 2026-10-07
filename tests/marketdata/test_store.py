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
