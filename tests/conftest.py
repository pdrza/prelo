from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import samples


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("corpus")
    return {
        "novel": samples.make_novel_pdf(base / "romance.pdf"),
        "magazine": samples.make_magazine_pdf(base / "revista.pdf"),
        "scanned": samples.make_scanned_pdf(base / "digitalizado.pdf"),
        "cbz": samples.make_cbz(base / "quadrinho.cbz"),
        "epub": samples.make_epub(base / "livro.epub"),
    }


@pytest.fixture
def out_dir(tmp_path) -> Path:
    d = tmp_path / "saida"
    d.mkdir()
    return d


@pytest.fixture
def calibre():
    exe = shutil.which("ebook-convert")
    if not exe:
        pytest.skip("Calibre não instalado: validação cruzada pulada")
    return exe
