"""Entrada MOBI/AZW3 e o "Adicionar livros" (filtros, pastas, links)."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from prelo.core.errors import DrmError, UnsupportedFormatError
from prelo.core.formats import BOOK_EXTENSIONS, BOOK_FILTERS, detect_format
from prelo.core.library import inspect_file
from prelo.core.pipeline import ConversionRequest, convert
from prelo.inputs.mobi import HuffCdic, KindleFile, _trailing_size


@pytest.fixture(scope="module")
def kindle_files(corpus, tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("kindle")
    return {fmt: Path(convert(ConversionRequest(corpus["epub"], fmt, output_dir=out)).output_path)
            for fmt in ("azw3", "mobi")}


def _epub_report(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        docs = [n for n in names if n.endswith(".xhtml") and not n.endswith("nav.xhtml")]
        bodies = "".join(z.read(n).decode() for n in docs)
        nav = z.read(next(n for n in names if n.endswith("nav.xhtml"))).decode()
    ids = set(re.findall(r'id="([^"]+)"', bodies))
    toc = re.findall(r'href="([^"]+)"', nav)
    return {"docs": len(docs), "bodies": bodies, "images": sum(n.endswith((".jpg", ".png")) for n in names),
            "toc": toc, "toc_ok": all("#" not in h or h.split("#")[1] in ids for h in toc)}


@pytest.mark.parametrize("fmt", ["azw3", "mobi"])
def test_kindle_de_volta_para_epub(kindle_files, tmp_path, fmt):
    src = kindle_files[fmt]
    assert detect_format(src) == "mobi"
    res = convert(ConversionRequest(src, "epub", output_dir=tmp_path))
    rep = _epub_report(Path(res.output_path))
    assert rep["docs"] >= 2 and rep["images"] >= 1
    assert rep["toc"] and rep["toc_ok"]
    assert "kindle:" not in rep["bodies"] and " aid=" not in rep["bodies"] and "filepos=" not in rep["bodies"]
    assert "Capítulo" in rep["bodies"] or "capítulo" in rep["bodies"].lower()


@pytest.mark.parametrize("fmt", ["azw3", "mobi"])
def test_kindle_na_estante(kindle_files, fmt):
    info, cover = inspect_file(kindle_files[fmt])
    assert info["format"] == "mobi"
    assert info["title"] == "Livro EPUB de Teste" and info["authors"] == ["Autor Teste"]
    assert cover is not None


def test_kindle_para_kindle_e_layout_fiel(kindle_files, tmp_path):
    res = convert(ConversionRequest(kindle_files["azw3"], "mobi", output_dir=tmp_path))
    assert KindleFile(Path(res.output_path).read_bytes()).header.version == 6
    res = convert(ConversionRequest(kindle_files["mobi"], "cbz", output_dir=tmp_path))  # pagina via EPUB
    with zipfile.ZipFile(res.output_path) as z:
        assert sum(n.endswith((".jpg", ".png")) for n in z.namelist()) >= 2


def test_kindle_com_drm_e_formatos_amazon_sem_suporte(kindle_files, tmp_path):
    data = bytearray(kindle_files["azw3"].read_bytes())
    rec0 = struct.unpack_from(">L", data, 78)[0]
    data[rec0 + 12:rec0 + 14] = b"\x00\x02"  # campo de criptografia do PalmDOC
    drm = tmp_path / "drm.azw3"
    drm.write_bytes(bytes(data))
    with pytest.raises(DrmError):
        detect_format(drm)
    for head, name in ((b"TPZ0" + b"\0" * 80, "livro.tpz"), (b"CONT" + b"\0" * 80, "livro.kfx")):
        (tmp_path / name).write_bytes(head)
        with pytest.raises(UnsupportedFormatError):
            detect_format(tmp_path / name)


def test_huffcdic_e_bytes_finais():
    # tabela em que cada byte é um código de 8 bits que aponta para a frase "ele mesmo"
    cache = struct.pack(">256L", *[(255 << 8) | 0x80 | 8] * 256)
    huff = b"HUFF\x00\x00\x00\x18" + struct.pack(">LL", 24, 24 + len(cache)) + b"\0" * 8 + cache + b"\0" * 256
    offsets, phrases = [], b""
    for v in range(256):
        offsets.append(2 * 256 + len(phrases))
        phrases += struct.pack(">H", 0x8000 | 1) + bytes([255 - v])
    cdic = b"CDIC\x00\x00\x00\x10" + struct.pack(">LL", 256, 8) + struct.pack(">256H", *offsets) + phrases
    texto = "Olá, mundo! ção".encode()
    assert HuffCdic([huff, cdic]).unpack(texto) == texto
    # texto | 1 byte de caractere repetido + contador (1) | entrada final de 3 bytes, cujo
    # tamanho fica no último byte (escrito de trás para frente)
    rec = b"conteudo" + b"\xc3\x01" + b"\xaa\xbb\x83"
    assert _trailing_size(rec, 0b11) == 5
    assert rec[:len(rec) - _trailing_size(rec, 0b11)] == b"conteudo"


def test_filtros_do_seletor():
    names = [n for n, _ in BOOK_FILTERS]
    assert names[0] == "Livros" and "Livros para Kindle" in names
    assert {".azw3", ".mobi", ".pdf", ".epub", ".cbz"} <= BOOK_EXTENSIONS
    from prelo.app.desktop import book_filters

    filtros = book_filters()
    assert "*.azw3" in filtros[0] and filtros[-1].startswith("Todos")
    # o seletor do GTK diferencia maiúsculas (vão as duas); o do Windows, não
    assert ("*.AZW3" in filtros[0]) == (sys.platform != "win32")


def test_pastas_e_caminhos_invalidos(kindle_files, tmp_path):
    from prelo.app.desktop import expand_paths

    pasta = tmp_path / "Livros"
    (pasta / "sub").mkdir(parents=True)
    shutil.copy(kindle_files["azw3"], pasta / "sub" / "a.azw3")
    (pasta / "notas.txt").write_text("não é livro")
    (pasta / ".oculta").mkdir()
    shutil.copy(kindle_files["mobi"], pasta / ".oculta" / "b.mobi")
    files, errors = expand_paths([str(pasta), str(tmp_path / "sumiu.pdf")])
    assert [f.name for f in files] == ["a.azw3"]
    assert len(errors) == 1 and "não existe" in errors[0]["message"]


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="no Windows, chmod não tira a permissão de leitura")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignora permissões")
def test_sem_permissao_de_leitura(kindle_files, tmp_path):
    from prelo.app.desktop import expand_paths

    f = tmp_path / "trancado.azw3"
    shutil.copy(kindle_files["azw3"], f)
    f.chmod(0)
    try:
        files, errors = expand_paths([str(f)])
    finally:
        f.chmod(0o644)
    assert not files and "permissão" in errors[0]["message"]


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="Calibre não instalado")
@pytest.mark.parametrize("tipo", ["old", "both"])
def test_mobi_feito_pelo_calibre(corpus, tmp_path, tipo):
    src = tmp_path / f"calibre-{tipo}.mobi"
    subprocess.run(["ebook-convert", str(corpus["epub"]), str(src), "--mobi-file-type", tipo],
                   check=True, capture_output=True, timeout=300)
    kf = KindleFile(src.read_bytes())
    assert kf.joint == (tipo == "both")
    rep = _epub_report(Path(convert(ConversionRequest(src, "epub", output_dir=tmp_path)).output_path))
    assert rep["images"] >= 1 and rep["toc"] and rep["toc_ok"]
