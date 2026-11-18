"""Durcissement du téléchargement de l'archive de données (app/data_sync.py), sans réseau ni Streamlit."""
import importlib.util
import io
import sys
import tarfile
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app" / "data_sync.py"


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


@pytest.fixture
def ds(monkeypatch):
    st = types.ModuleType("streamlit")
    st.secrets = {}
    st.cache_data = lambda **kw: (lambda f: f)
    req = types.ModuleType("requests")
    req.get = None
    req.Response = FakeResponse
    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.setitem(sys.modules, "requests", req)
    spec = importlib.util.spec_from_file_location("data_sync_under_test", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b"x"
        info = tarfile.TarInfo("root-abc/a.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_pas_d_authorization_sans_jeton(ds, monkeypatch):
    seen = {}
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: seen.update(kw) or FakeResponse())
    ds._gh("/x", "a/b")
    assert "Authorization" not in seen["headers"]


def test_authorization_avec_jeton(ds, monkeypatch):
    seen = {}
    ds.st.secrets = {"DATA_REPO_TOKEN": "tok"}
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: seen.update(kw) or FakeResponse())
    ds._gh("/x", "a/b")
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_archive_trop_grosse_content_length(ds, monkeypatch, tmp_path):
    monkeypatch.setattr(ds, "MAX_ARCHIVE_BYTES", 10)
    monkeypatch.setattr(ds.requests, "get", lambda *a, **k: FakeResponse(b"x", headers={"Content-Length": "11"}))
    with pytest.raises(RuntimeError, match="trop volumineuse"):
        ds._download("abc", tmp_path / "data")


def test_archive_trop_grosse_flux(ds, monkeypatch, tmp_path):
    monkeypatch.setattr(ds, "MAX_ARCHIVE_BYTES", 10)
    monkeypatch.setattr(ds.requests, "get", lambda *a, **k: FakeResponse(b"x" * 20))
    with pytest.raises(RuntimeError, match="trop volumineuse"):
        ds._download("abc", tmp_path / "data")


def test_espace_disque_insuffisant(ds, monkeypatch, tmp_path):
    monkeypatch.setattr(ds.requests, "get", lambda *a, **k: FakeResponse(_tarball()))
    monkeypatch.setattr(ds.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=1))
    with pytest.raises(RuntimeError, match="espace disque"):
        ds._download("abc", tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_telechargement_nominal(ds, monkeypatch, tmp_path):
    monkeypatch.setattr(ds.requests, "get", lambda *a, **k: FakeResponse(_tarball()))
    data = tmp_path / "data"
    ds._download("abc", data)
    assert (data / "a.txt").read_bytes() == b"x"
    assert (data / ds.MARKER).read_text(encoding="utf-8") == "abc"
