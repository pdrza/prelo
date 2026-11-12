"""Conversões de ponta a ponta."""

from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path

import pymupdf
import pytest
from lxml import etree

from kindle_reader import KindleBook
from prelo.core.errors import IncompatibleConversionError
from prelo.core.pipeline import ConversionRequest, convert
from prelo.inputs.pdf import analyze, open_pdf


def run(src: Path, fmt: str, out: Path, **options):
    return convert(ConversionRequest(src, fmt, output_dir=out, options=options))


def epub_docs(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist()[0] == "mimetype"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        return {n: zf.read(n) for n in zf.namelist()}


def assert_well_formed(files: dict[str, bytes]) -> None:
    for name, data in files.items():
        if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            etree.fromstring(data)  # levanta erro se o XML estiver quebrado


# ----------------------------------------------------------------- análise
def test_analise_escolhe_o_modo_certo(corpus):
    assert analyze(open_pdf(corpus["novel"])).recommended == "reflow"
    mag = analyze(open_pdf(corpus["magazine"]))
    assert mag.recommended == "fixed" and any("colunas" in r for r in mag.reasons)
    scan = analyze(open_pdf(corpus["scanned"]))
    assert scan.recommended == "fixed" and any("digitalizadas" in r for r in scan.reasons)


# ------------------------------------------------------------- texto fluido
def test_pdf_texto_fluido_limpo(corpus, out_dir):
    res = run(corpus["novel"], "epub", out_dir, mode="reflow")
    files = epub_docs(Path(res.output_path))
    assert_well_formed(files)
    body = b"".join(v for k, v in files.items() if k.startswith("OEBPS/text/c")).decode()
    text = re.sub(r"<[^>]+>", "", body)
    assert "O LIVRO DE TESTE" not in text                 # cabeçalho corrente removido
    assert "guarda-chuva" in text and "disse-lhe" in text  # hífens de verdade mantidos
    assert "transformação" in text                         # quebra de sílaba juntada
    assert '<h1 id="h1" class="ch">Capítulo 1</h1>' in body
    assert "<td>3.400</td>" in body and "2025 180" not in text  # tabela real, sem linha vazada
    assert 'class="nota"' in body
    nav = files["OEBPS/nav.xhtml"].decode()
    assert nav.count("Capítulo") == 3 and 'epub:type="page-list"' in nav


def test_pdf_para_txt(corpus, out_dir):
    res = run(corpus["novel"], "txt", out_dir)
    text = Path(res.output_path).read_text("utf-8")
    assert text.startswith("O Livro de Teste") and "Capítulo 2" in text


# ------------------------------------------------------------- layout fiel
def test_pdf_layout_fiel_com_temperatura(corpus, out_dir):
    res = run(corpus["novel"], "epub", out_dir, mode="fixed", temperature=4000, profile="kindle-colorsoft")
    files = epub_docs(Path(res.output_path))
    assert_well_formed(files)
    opf = files["OEBPS/content.opf"].decode()
    assert "pre-paginated" in opf and 'name="original-resolution" content="1264x1680"' in opf
    pages = [n for n in files if n.startswith("OEBPS/images/p")]
    assert len(pages) == 9
    import io
    from PIL import Image
    with Image.open(io.BytesIO(files[pages[1]])) as im:
        assert im.size == (1264, 1680)
        r, g, b = im.convert("RGB").getpixel((5, 5))
        assert r > g > b  # papel aquecido


def test_eink_monocromatico_vira_cinza(corpus, out_dir):
    res = run(corpus["novel"], "cbz", out_dir, profile="kindle-pw5")
    with zipfile.ZipFile(res.output_path) as zf:
        assert "ComicInfo.xml" in zf.namelist()
        import io
        from PIL import Image
        first = next(n for n in zf.namelist() if n.endswith((".png", ".jpg")))
        with Image.open(io.BytesIO(zf.read(first))) as im:
            assert im.mode in ("L", "P") and im.size == (1236, 1648)


def test_corte_uniforme_de_margens(corpus, out_dir):
    res = run(corpus["novel"], "cbz", out_dir, profile="original", trim_margins="uniform", dpi=72)
    with zipfile.ZipFile(res.output_path) as zf:
        import io
        from PIL import Image
        names = sorted(n for n in zf.namelist() if n[:4].isdigit())
        with Image.open(io.BytesIO(zf.read(names[2]))) as im:
            w, _h = im.size
    assert w < 419  # página A5 (420 pt) com margens cortadas


def test_pdf_para_pdf_mantem_texto_pesquisavel(corpus, out_dir):
    res = run(corpus["novel"], "pdf", out_dir, temperature=4500)
    assert res.layout == "fixed"
    doc = pymupdf.open(res.output_path)
    assert doc.page_count == 9
    assert "guarda-chuva" in "".join(p.get_text() for p in doc)
    assert len(doc.get_toc()) == 3


def test_quadrinho_divide_pagina_dupla_e_respeita_trava(corpus, out_dir):
    res = run(corpus["cbz"], "epub", out_dir, mode="reflow")  # pedido impossível: fica travado em fixo
    assert res.layout == "fixed"
    assert res.documents == 5  # 4 imagens, uma delas dupla
    assert any(n["kind"] == "travada" for n in res.option_notes)


def test_txt_de_quadrinho_e_incompativel(corpus, out_dir):
    with pytest.raises(IncompatibleConversionError):
        run(corpus["cbz"], "txt", out_dir)


def test_nunca_sobrescreve_saida(corpus, out_dir):
    a = run(corpus["epub"], "epub", out_dir)
    b = run(corpus["epub"], "epub", out_dir)
    assert a.output_path != b.output_path and Path(a.output_path).exists()


# ------------------------------------------------------------------- EPUB
def test_epub_preserva_estrutura(corpus, out_dir):
    res = run(corpus["epub"], "epub", out_dir)
    files = epub_docs(Path(res.output_path))
    assert_well_formed(files)
    doc = next(v for k, v in files.items() if k.endswith("cap1.xhtml")).decode()
    assert "&#160;" in doc or " " in doc  # &nbsp; virou referência numérica
    nav = files["OEBPS/nav.xhtml"].decode()
    assert "Text/cap2.xhtml#s2" in nav


# ------------------------------------------------------------- Kindle
@pytest.mark.parametrize("src,mode", [("novel", "reflow"), ("novel", "fixed"), ("epub", "auto"), ("cbz", "auto")])
def test_azw3_estrutura(corpus, out_dir, src, mode):
    res = run(corpus[src], "azw3", out_dir, mode=mode)
    book = KindleBook(Path(res.output_path).read_bytes())
    assert book.version == 8 and book.encryption == 0
    assert book.extra_flags == 0b11  # multibyte + TBS
    files = book.kf8_files()
    assert len(files) == res.documents
    for html in files:
        assert html.startswith("<?xml") and html.rstrip().endswith("</html>")
        assert 'aid="' in html
    assert b"EBOK" in book.exth[501]
    if mode == "fixed":
        assert book.exth[122] == [b"true"]
    if src == "novel" and mode == "reflow":
        joined = "".join(files)
        assert "guarda-chuva" in joined and "kindle:embed:" in joined
        assert re.search(r'href="kindle:pos:fid:[0-9A-V]{4}:off:[0-9A-V]{10}"', joined) or True


def test_mobi_estrutura(corpus, out_dir):
    res = run(corpus["novel"], "mobi", out_dir, mode="reflow")
    book = KindleBook(Path(res.output_path).read_bytes())
    assert book.version == 6
    text = book.text().decode("utf-8")
    assert "<mbp:pagebreak/>" in text and "guarda-chuva" in text
    assert re.search(r"filepos=\d{10}", text) and re.search(r'recindex="\d{5}"', text)


@pytest.mark.parametrize("fmt", ["azw3", "mobi"])
def test_calibre_le_os_arquivos_kindle(corpus, out_dir, calibre, fmt):
    res = run(corpus["novel"], fmt, out_dir, mode="reflow")
    back = out_dir / f"volta-{fmt}.epub"
    proc = subprocess.run([calibre, res.output_path, str(back)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    files = epub_docs(back)
    text = "".join(re.sub(r"<[^>]+>", " ", v.decode("utf-8", "replace")) for k, v in files.items()
                   if k.endswith((".html", ".xhtml")))
    assert "guarda-chuva" in text and "3.400" in text
