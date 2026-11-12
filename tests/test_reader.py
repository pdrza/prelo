"""Leitor: todos os formatos, repaginação pela letra, onde parou e a API."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image, ImageStat

import samples
from prelo.api.server import create_app
from prelo.core.formats import comic_pages
from prelo.core.imaging import Adjustments
from prelo.core.library import Library
from prelo.core.pipeline import ConversionRequest, convert
from prelo.core.preview import PreviewRenderer
from prelo.core.reader import Reader
from prelo.inputs.epub import is_fixed_layout

TOKEN = "segredo-de-teste"


def livro(path: Path, fmt: str, reading: dict | None = None, ident: str | None = None):
    """O que o leitor usa de um livro da estante."""
    return SimpleNamespace(id=ident or path.stem, path=str(path), format=fmt, reading=reading or {})


def palavras(reader: Reader, book_id: str, page: int) -> list[str]:
    return reader._books[book_id].doc[page - 1].get_text("text").split()


def jpeg(data: bytes) -> Image.Image:
    assert data[:2] == b"\xff\xd8"
    return Image.open(io.BytesIO(data))


def capitulo(info: dict, n: int) -> int:
    return next(t["page"] for t in info["toc"] if t["title"] == f"Capítulo {n}")


@pytest.fixture(scope="module")
def longo(tmp_path_factory) -> Path:
    return samples.make_long_epub(tmp_path_factory.mktemp("leitor") / "longo.epub")


def test_epub_repagina_com_a_letra(longo, tmp_path):
    r = Reader(tmp_path)
    e = livro(longo, "epub")
    pequena = r.info(e, 480, 720, 14)
    grande = r.info(e, 480, 720, 28)
    assert pequena["kind"] == grande["kind"] == "reflow"
    assert grande["pages"] > pequena["pages"] * 2
    assert [t["title"] for t in grande["toc"]] == [f"Capítulo {n}" for n in range(1, 5)]
    paginas = [t["page"] for t in grande["toc"]]
    assert paginas[0] == 1 and paginas == sorted(paginas)
    assert jpeg(r.render(e, 3, 480, 720, 28, 2.0)).size == (960, 1440)  # nítida na densidade da tela
    r.close()


def test_onde_parou_sobrevive_a_letra_e_a_reabrir(longo, tmp_path):
    r = Reader(tmp_path)
    e = livro(longo, "epub")
    pagina = capitulo(r.info(e, 480, 720, 18), 3) + 3
    estado = r.position(e, pagina, 480, 720, 18)
    assert estado["chapter"] == 2 and estado["offset"] > 0 and estado["font_size"] == 18
    inicio = palavras(r, e.id, pagina)[:3]
    r.close()

    outro = Reader(tmp_path)  # como fechar o app e abrir de novo, com outra letra e outra janela
    e = livro(longo, "epub", reading=estado)
    info = outro.info(e, 520, 760, 26)
    assert capitulo(info, 3) <= info["page"] < capitulo(info, 4)
    nova = palavras(outro, e.id, info["page"])
    texto = nova + palavras(outro, e.id, info["page"] + 1)
    # a página reaberta é a que contém o começo da página onde parou
    assert any(texto[i:i + 3] == inicio for i in range(len(nova))), (inicio, nova[:12])
    outro.close()


def test_links_internos(longo, tmp_path):
    r = Reader(tmp_path)
    e = livro(longo, "epub")
    info = r.info(e, 480, 720, 18)
    links = r.links(e, 1, 480, 720, 18)
    assert [link["page"] for link in links] == [capitulo(info, 4)]
    assert all(0 <= link["x"] < 1 and 0 <= link["y"] < 1 and link["w"] > 0 for link in links)
    r.close()


def test_tema_noite_escurece_a_pagina(longo, tmp_path):
    r = Reader(tmp_path)
    e = livro(longo, "epub")
    r.info(e, 480, 720, 18)
    claro = jpeg(r.render(e, 2, 480, 720, 18, 1.0)).convert("L")
    noite = jpeg(r.render(e, 2, 480, 720, 18, 1.0, Adjustments(invert=True, temperature=4200))).convert("L")
    assert ImageStat.Stat(claro).mean[0] > 200 and ImageStat.Stat(noite).mean[0] < 60
    r.close()


def test_pdf_quadrinho_e_imagem_sao_fixos(corpus, tmp_path):
    r = Reader(tmp_path)
    pdf = livro(corpus["novel"], "pdf")
    info = r.info(pdf, 600, 800, 18)
    assert info["kind"] == "fixed" and info["pages"] == 9 and len(info["ratios"]) == 9
    assert info["ratios"][0] == pytest.approx(420 / 595, abs=0.01) and len(info["toc"]) == 3
    img = jpeg(r.render(pdf, 2, 300, 500, 18, 2.0))  # caixa de 300 × 500, densidade 2
    assert img.width <= 601 and img.height <= 1001
    assert abs(img.width - 600) <= 1 or abs(img.height - 1000) <= 1  # encosta num lado, sem distorcer

    cbz = r.info(livro(corpus["cbz"], "comic"), 600, 800, 18)
    assert cbz["kind"] == "fixed" and cbz["pages"] == 4 and cbz["toc"] == []
    assert cbz["ratios"][2] > 1  # a página dupla é deitada: o leitor a mostra sozinha
    assert jpeg(r.render(livro(corpus["cbz"], "comic"), 1, 200, 300, 18, 1.0)).size == (200, 300)

    foto = tmp_path / "foto.jpg"
    Image.new("RGB", (800, 600), (200, 150, 90)).save(foto)
    info = r.info(livro(foto, "image"), 600, 800, 18)
    assert info["pages"] == 1 and info["ratios"] == [pytest.approx(800 / 600, abs=0.001)]
    r.close()


def test_epub_de_layout_fixo(corpus, tmp_path):
    fixo = Path(convert(ConversionRequest(corpus["novel"], "epub", output_dir=tmp_path,
                                          options={"mode": "fixed", "profile": "kindle-pw5"})).output_path)
    r = Reader(tmp_path / "dados")
    info = r.info(livro(fixo, "epub"), 600, 800, 18)
    assert info["kind"] == "fixed" and info["pages"] == 9
    assert info["ratios"][0] == pytest.approx(1236 / 1648, abs=0.001)
    r.close()


OPF = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata>{meta}</metadata>'
       '<spine>{refs}</spine></package>')
CAPA_FIXA = '<itemref idref="capa" properties="rendition:layout-pre-paginated"/>'


@pytest.mark.parametrize(("meta", "refs", "fixo"), [
    ("", CAPA_FIXA + '<itemref idref="c1"/>' * 5, False),  # só a capa é pré-paginada: livro fluido
    ('<meta property="rendition:layout">pre-paginated</meta>', '<itemref idref="c1"/>', True),
    ('<meta name="fixed-layout" content="true"/>', '<itemref idref="c1"/>', True),  # metadado da Amazon
    ("", CAPA_FIXA * 3 + '<itemref idref="fim"/>', True),  # a maioria das páginas, uma a uma
    ('<meta property="rendition:layout">reflowable</meta>', '<itemref idref="c1"/>', False),
])
def test_layout_fixo_so_quando_e_do_livro_todo(meta, refs, fixo):
    assert is_fixed_layout(etree.fromstring(OPF.format(meta=meta, refs=refs).encode())) is fixo


def test_capa_pre_paginada_nao_prende_a_letra(longo, tmp_path):
    capa = tmp_path / "capa-fixa.epub"
    with zipfile.ZipFile(longo) as zin, zipfile.ZipFile(capa, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".opf"):
                data = data.replace(b"<itemref ", b'<itemref properties="rendition:layout-pre-paginated" ', 1)
            zout.writestr(item, data)
    r = Reader(tmp_path / "dados")
    assert r.info(livro(capa, "epub"), 480, 720, 18)["kind"] == "reflow"
    r.close()


def test_quadrinho_tem_as_mesmas_paginas_em_todo_lugar(corpus, tmp_path):
    cbz = tmp_path / "compactado-no-mac.cbz"
    shutil.copy(corpus["cbz"], cbz)
    with zipfile.ZipFile(cbz, "a") as z:
        png = z.read(comic_pages(z.namelist())[0])
        z.writestr("._0001.png", png)  # o macOS cria esses ao compactar
        z.writestr("__MACOSX/._0002.png", png)
        z.writestr(".oculta/0003.png", png)
    entry = Library(tmp_path / "dados").add(cbz)
    assert entry.pages == 4
    r = Reader(tmp_path / "dados")
    assert r.info(entry, 600, 800, 18)["pages"] == 4
    r.close()
    previa = PreviewRenderer()
    assert previa.page_count(entry.id, entry.path, entry.format) == 4
    previa.close()


def test_posicao_fica_fora_da_estante(longo, tmp_path):
    # a posição muda a cada página virada: vai para um arquivo pequeno, não para o library.json
    lib = Library(tmp_path)
    entry = lib.add(longo)
    lib.update_reading(entry.id, {"page": 7, "chapter": 1, "offset": 120})
    assert json.loads((tmp_path / "leitura.json").read_text("utf-8")) == {
        entry.id: {"page": 7, "chapter": 1, "offset": 120}}
    estante = json.loads((tmp_path / "library.json").read_text("utf-8"))
    assert all("reading" not in b for b in estante["books"])
    assert Library(tmp_path).get(entry.id).reading["page"] == 7

    # versão 0.2: a posição ficava dentro do library.json
    estante["books"][0]["reading"] = {"page": 3}
    (tmp_path / "library.json").write_text(json.dumps(estante), "utf-8")
    (tmp_path / "leitura.json").unlink()
    assert Library(tmp_path).get(entry.id).reading == {"page": 3}
    assert json.loads((tmp_path / "leitura.json").read_text("utf-8")) == {entry.id: {"page": 3}}


def test_kindle_vira_epub_uma_vez(corpus, tmp_path):
    azw3 = Path(convert(ConversionRequest(corpus["epub"], "azw3", output_dir=tmp_path)).output_path)
    r = Reader(tmp_path / "dados")
    e = livro(azw3, "mobi", ident="kindle")
    info = r.info(e, 480, 720, 18)
    assert info["kind"] == "reflow" and info["pages"] >= 2
    cache = tmp_path / "dados" / "leitura" / "kindle.epub"
    antes = cache.stat().st_mtime_ns
    outro = Reader(tmp_path / "dados")  # outra sessão: usa o EPUB já feito
    outro.info(e, 480, 720, 20)
    outro.close()
    assert cache.stat().st_mtime_ns == antes
    r.forget("kindle")  # tirar da estante apaga o EPUB feito para ler
    assert not cache.exists()


def test_previa_do_kindle_usa_o_epub_do_leitor(corpus, tmp_path):
    azw3 = Path(convert(ConversionRequest(corpus["epub"], "azw3", output_dir=tmp_path)).output_path)
    pasta = tmp_path / "dados"
    with TestClient(create_app(token=TOKEN, mode="desktop", folder=pasta)) as c:
        c.headers["X-Prelo-Token"] = TOKEN
        book = c.post("/api/books", json={"paths": [str(azw3)]}).json()["added"][0]
        assert c.get(f"/api/books/{book['id']}/pages").json()["count"] >= 2
        feito = pasta / "leitura" / f"{book['id']}.epub"
        antes = feito.stat().st_mtime_ns
        pagina = c.get(f"/api/books/{book['id']}/pages/1/preview", params={"w": 300})
        assert abs(jpeg(pagina.content).width - 300) <= 1
        c.get(f"/api/books/{book['id']}/reader", params={"w": 480, "h": 720, "fs": 18})
        assert feito.stat().st_mtime_ns == antes  # prévia e leitor: uma conversão só
        assert c.delete(f"/api/books/{book['id']}").status_code == 200
        assert not feito.exists()


def test_api_do_leitor(longo, tmp_path):
    pasta = tmp_path / "dados"
    with TestClient(create_app(token=TOKEN, mode="desktop", folder=pasta)) as c:
        c.headers["X-Prelo-Token"] = TOKEN
        book = c.post("/api/books", json={"paths": [str(longo)]}).json()["added"][0]
        url = f"/api/books/{book['id']}/reader"
        medida = {"w": 480, "h": 720, "fs": 18}
        info = c.get(url, params=medida).json()
        assert info["kind"] == "reflow" and info["page"] == 1
        pg = c.get(f"{url}/pages/2", params={**medida, "dpr": 1.5, "temperature": 4300})
        assert pg.headers["content-type"] == "image/jpeg" and jpeg(pg.content).size == (720, 1080)
        assert c.get(f"{url}/pages/0", params=medida).status_code == 422
        assert c.get("/api/books/nao-existe/reader", params=medida).status_code == 404
        assert c.get(f"{url}/pages/1/links", params=medida).json()[0]["page"] == capitulo(info, 4)
        alvo = capitulo(info, 3) + 1
        salvo = c.put(f"{url}/position", json={"page": alvo, **medida}).json()
        assert salvo["chapter"] == 2 and c.get(f"/api/books/{book['id']}").json()["reading"]["chapter"] == 2

    with TestClient(create_app(token=TOKEN, mode="desktop", folder=pasta)) as c:  # app aberto de novo
        c.headers["X-Prelo-Token"] = TOKEN
        assert c.get(url, params=medida).json()["page"] == alvo


def test_preferencias_do_leitor_ficam_na_api(tmp_path):
    # a janela do app não guarda localStorage: tema, páginas e letra ficam nas preferências
    with TestClient(create_app(token=TOKEN, mode="desktop", folder=tmp_path)) as c:
        c.headers["X-Prelo-Token"] = TOKEN
        pedido = {"theme": "noite", "spread": "2", "font_size": 22, "inventada": 1}
        assert c.put("/api/settings", json={"reader": pedido}).json()["reader"] == {
            "theme": "noite", "spread": "2", "font_size": 22}
        estranho = {"theme": "neon", "spread": "9", "font_size": 400}
        assert c.put("/api/settings", json={"reader": estranho}).json()["reader"] == {}
        c.put("/api/settings", json={"reader": {"theme": "sepia"}})
    with TestClient(create_app(token=TOKEN, mode="desktop", folder=tmp_path)) as c:
        c.headers["X-Prelo-Token"] = TOKEN
        assert c.get("/api/settings").json()["reader"] == {"theme": "sepia"}
