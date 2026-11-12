"""API HTTP: estante, prévia, fila e segurança."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from prelo.api.server import create_app

TOKEN = "segredo-de-teste"


@pytest.fixture
def client(tmp_path):
    app = create_app(token=TOKEN, mode="desktop", folder=tmp_path / "dados")
    with TestClient(app) as c:
        c.headers["X-Prelo-Token"] = TOKEN
        yield c


def test_token_obrigatorio(tmp_path):
    app = create_app(token=TOKEN, mode="desktop", folder=tmp_path / "d")
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/books").status_code == 401
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert r.status_code == 303 and "prelo_token" in r.headers["set-cookie"]


def test_catalogos(client):
    assert {f["id"] for f in client.get("/api/formats").json()["outputs"]} >= {"epub", "azw3", "mobi"}
    assert any(p["id"] == "kindle-pw5" for p in client.get("/api/profiles").json())
    assert any(o["name"] == "temperature" for o in client.get("/api/options").json())


def test_fluxo_completo(client, corpus, tmp_path):
    r = client.post("/api/books", json={"paths": [str(corpus["novel"])]})
    book = r.json()["added"][0]
    assert book["format"] == "pdf" and book["pages"] == 9 and book["has_cover"]
    assert book["analysis"]["recommended"] == "reflow"
    # mesmo arquivo de novo: não duplica
    assert client.post("/api/books", json={"paths": [str(corpus["novel"])]}).json()["added"][0]["id"] == book["id"]
    assert client.get(f"/api/books/{book['id']}/cover").headers["content-type"] == "image/jpeg"
    prev = client.get(f"/api/books/{book['id']}/pages/2/preview", params={"w": 300, "temperature": 3500})
    assert prev.status_code == 200 and prev.content[:2] == b"\xff\xd8"
    # metadados e configuração salva do livro
    client.patch(f"/api/books/{book['id']}", json={"metadata": {"title": "Título Novo"},
                                                     "settings": {"temperature": 5000}})
    job = client.post("/api/jobs", json={"book_id": book["id"], "output_format": "epub",
                                         "output_dir": str(tmp_path / "out"), "options": {"mode": "fixed",
                                                                                        "profile": "tablet"}}).json()
    for _ in range(240):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.25)
    assert job["status"] == "done", job
    assert job["result"]["title"] == "Título Novo"
    assert any(o["option"] == "temperature" and o["source"] == "configuração do livro"
               for o in job["result"]["options"])
    dl = client.get(f"/api/jobs/{job['id']}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    hist = client.get(f"/api/books/{book['id']}").json()["conversions"]
    assert hist and hist[0]["output_format"] == "epub"


def test_opcao_errada_responde_400(client, corpus):
    book = client.post("/api/books", json={"paths": [str(corpus["epub"])]}).json()["added"][0]
    r = client.post("/api/jobs", json={"book_id": book["id"], "output_format": "azw3",
                                       "options": {"temperatura": 4000}})
    assert r.status_code == 400 and r.json()["code"] == "opcao_desconhecida"


def test_modo_servidor_recusa_caminhos(tmp_path, corpus):
    app = create_app(token=None, mode="server", folder=tmp_path / "srv")
    with TestClient(app) as c:
        assert c.post("/api/books", json={"paths": [str(corpus["novel"])]}).status_code == 403
        with open(corpus["epub"], "rb") as fh:
            up = c.post("/api/books/upload", files={"files": ("livro.epub", fh, "application/epub+zip")}).json()
        assert up["added"][0]["format"] == "epub"
        r = c.post("/api/jobs", json={"book_id": up["added"][0]["id"], "output_format": "epub",
                                      "output_dir": "/etc"})
        assert r.status_code == 403
