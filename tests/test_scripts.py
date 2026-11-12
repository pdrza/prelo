"""Utilitários (prelo-lote, prelo-enviar, prelo-vigiar), rodando de verdade.

Os mesmos testes valem para as duas versões: bash (``bin/``) no Linux e PowerShell
(``windows/bin/``, pelos atalhos ``.cmd``) no Windows. Opções, mensagens e
registros são os mesmos.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"
BIN = RAIZ / "windows" / "bin" if WINDOWS else RAIZ / "bin"
# a pasta dos comandos instalados pelo pip: num venv, a do python.exe; num Python
# instalado no Windows (como na CI), a Scripts\ ao lado dele
PRELO = Path(sysconfig.get_path("scripts")) / ("prelo.exe" if WINDOWS else "prelo")


def rodar(cmd: str, *args, tmp: Path, **env) -> subprocess.CompletedProcess:
    ambiente = {**os.environ, "PRELO_BIN": str(PRELO),
                "XDG_STATE_HOME": str(tmp / "estado"), "PRELO_DATA_DIR": str(tmp / "dados"),
                "DISPLAY": "", "WAYLAND_DISPLAY": "", "DBUS_SESSION_BUS_ADDRESS": "",  # sem notificações
                "PRELO_NOTIFICAR": "0", **env}
    programa = BIN / (f"{cmd}.cmd" if WINDOWS else cmd)
    return subprocess.run([str(programa), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=ambiente, timeout=300)


def sem_escrita(pasta: Path) -> None:
    if WINDOWS:  # nega "criar arquivos" a todos (S-1-1-0), inclusive a quem roda o teste
        subprocess.run(["icacls", str(pasta), "/deny", "*S-1-1-0:(W)"], check=True, capture_output=True)
    else:
        pasta.chmod(0o555)


def com_escrita(pasta: Path) -> None:
    if WINDOWS:
        subprocess.run(["icacls", str(pasta), "/remove:d", "*S-1-1-0"], check=True, capture_output=True)
    else:
        pasta.chmod(0o755)


@pytest.fixture
def livros(tmp_path, corpus) -> Path:
    pasta = tmp_path / "livros"
    (pasta / "sub").mkdir(parents=True)
    shutil.copy(corpus["novel"], pasta / "romance.pdf")
    shutil.copy(corpus["epub"], pasta / "sub" / "livro.epub")
    (pasta / "anotacoes.txt").write_text("não é livro", encoding="utf-8")
    return pasta


def test_ajuda_de_todos(tmp_path):
    for cmd in ("prelo-lote", "prelo-enviar", "prelo-vigiar"):
        r = rodar(cmd, "--ajuda", tmp=tmp_path)
        assert r.returncode == 0 and "Uso:" in r.stdout


def test_opcao_desconhecida(tmp_path):
    r = rodar("prelo-lote", "--xyz", tmp=tmp_path)
    assert r.returncode == 1 and "opção desconhecida: --xyz" in r.stderr


def test_lote_converte_e_nao_repete(livros, tmp_path):
    saida = tmp_path / "saida"
    r = rodar("prelo-lote", "-r", "-f", "azw3", "-o", saida, livros, tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(list(saida.glob("*.azw3"))) == 2
    assert "2 convertido(s)" in r.stderr
    # segunda vez: nada novo (o manifesto reconhece pelo conteúdo, não pelo nome)
    (livros / "romance.pdf").rename(livros / "renomeado.pdf")
    r = rodar("prelo-lote", "-r", "-f", "azw3", "-o", saida, livros, tmp=tmp_path)
    assert "0 convertido(s), 2 já convertido(s)" in r.stderr
    assert len(list(saida.glob("*.azw3"))) == 2


def test_lote_nao_entra_na_pasta_de_saida(livros, tmp_path, corpus):
    # a saída dentro da entrada, escrita com a barra no fim: o que já está lá não é entrada
    saida = livros / "convertidos"
    saida.mkdir()
    shutil.copy(corpus["epub"], saida / "de-antes.epub")
    r = rodar("prelo-lote", "-r", "-f", "azw3", "-o", f"{saida}{os.sep}", livros, tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "2 convertido(s)" in r.stderr
    assert not (saida / "de-antes.azw3").exists()


def test_lote_em_paralelo_e_com_extras(livros, tmp_path):
    saida = tmp_path / "saida"
    r = rodar("prelo-lote", "-r", "-j", "2", "-f", "epub", "--mesmo-formato", "-o", saida, livros,
              "--", "--authors", "Ana Lúcia, João", tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "2 convertido(s)" in r.stderr
    gerados = list(saida.glob("*.epub"))
    assert len(gerados) == 2
    for epub in gerados:  # o extra (com espaço, vírgula e acento) chegou intacto ao prelo convert
        with zipfile.ZipFile(epub) as z:
            assert "<dc:creator" in (opf := z.read("OEBPS/content.opf").decode()) and "Ana Lúcia" in opf, opf


def test_lote_simular_e_formato_igual(livros, tmp_path):
    r = rodar("prelo-lote", "-n", "-r", "-f", "epub", livros, tmp=tmp_path)
    assert r.returncode == 0
    assert "romance.pdf" in r.stdout and "livro.epub" not in r.stdout  # EPUB → EPUB fica de fora


def test_enviar_para_kindle_e_kobo(livros, tmp_path, corpus):
    midia = tmp_path / "media"
    kindle = midia / "Kindle"
    (kindle / "documents").mkdir(parents=True)
    (kindle / "system").mkdir()
    r = rodar("prelo-enviar", "--listar", tmp=tmp_path, PRELO_MONTAGENS=str(midia))
    assert r.returncode == 0 and "kindle" in r.stdout
    r = rodar("prelo-enviar", livros / "romance.pdf", tmp=tmp_path, PRELO_MONTAGENS=str(midia))
    assert r.returncode == 0, r.stderr
    assert len(list((kindle / "documents").glob("*.azw3"))) == 1
    # mesmo livro de novo: não duplica
    r = rodar("prelo-enviar", livros / "romance.pdf", tmp=tmp_path, PRELO_MONTAGENS=str(midia))
    assert len(list((kindle / "documents").glob("*.azw3"))) == 1

    kobo = tmp_path / "KOBOeReader"
    (kobo / ".kobo").mkdir(parents=True)
    r = rodar("prelo-enviar", "-d", kobo, livros / "sub" / "livro.epub", tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (kobo / "livro.epub").read_bytes() == corpus["epub"].read_bytes()  # EPUB vai como está


def test_enviar_sem_leitor_explica(livros, tmp_path):
    r = rodar("prelo-enviar", livros / "romance.pdf", tmp=tmp_path, PRELO_MONTAGENS=str(tmp_path / "vazio"))
    assert r.returncode == 1 and "nenhum Kindle ou Kobo" in r.stderr


def test_vigiar_uma_vez(livros, tmp_path):
    entrada, saida = tmp_path / "entrada", tmp_path / "saida"
    entrada.mkdir()
    shutil.copy(livros / "romance.pdf", entrada / "novo.pdf")
    r = rodar("prelo-vigiar", "--uma-vez", "-f", "epub", entrada, saida, tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(list(saida.glob("*.epub"))) == 1
    r = rodar("prelo-vigiar", "--uma-vez", "-f", "epub", entrada, saida, tmp=tmp_path)
    assert len(list(saida.glob("*.epub"))) == 1  # não converte de novo


@pytest.mark.parametrize(("livro", "extras"), [("epub", []), ("novel", ["--mode", "reflow"])])
def test_cli_print_path(corpus, tmp_path, livro, extras):
    # o texto fluido de PDF procura tabelas, e o PyMuPDF escreve um aviso na saída padrão
    r = subprocess.run([str(PRELO), "convert", str(corpus[livro]), "-f", "azw3", "-o", str(tmp_path),
                        "--print-path", *extras], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    linhas = r.stdout.splitlines()
    assert len(linhas) == 1 and Path(linhas[0]).exists() and linhas[0].endswith(".azw3"), r.stdout


def test_cli_erro_redirecionado_em_utf8(tmp_path):
    # no Windows, a saída redirecionada usaria a página de código ANSI, que não tem o ✗
    nao_livro = tmp_path / "anotações.txt"
    nao_livro.write_text("não é livro", encoding="utf-8")
    r = subprocess.run([str(PRELO), "convert", str(nao_livro), "-f", "epub"], capture_output=True,
                       timeout=120)
    assert r.returncode == 1
    assert "✗ " in r.stderr.decode("utf-8") and "anotações.txt" in r.stderr.decode("utf-8")


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignora a permissão de escrita")
def test_enviar_avisa_quando_a_copia_falha(livros, tmp_path):
    kobo = tmp_path / "KOBOeReader"
    (kobo / ".kobo").mkdir(parents=True)
    sem_escrita(kobo)  # leitor sem espaço de escrita: a cópia falha
    try:
        r = rodar("prelo-enviar", "-d", kobo, livros / "sub" / "livro.epub", tmp=tmp_path)
    finally:
        com_escrita(kobo)
    assert r.returncode == 1
    assert "não foi possível copiar" in r.stderr and "0 enviado(s), 1 com erro" in r.stderr
    assert not list(kobo.glob("*.epub*"))


def _extensoes(arquivo: Path, prefixo: str) -> set[str]:
    linha = next(ln for ln in arquivo.read_text(encoding="utf-8-sig").splitlines() if ln.startswith(prefixo))
    aspas = "'" if "'" in linha.split("#")[0] else '"'
    return set(linha.split(aspas)[1].split())


def test_extensoes_dos_utilitarios_iguais_as_do_prelo():
    from prelo.core.formats import BOOK_EXTENSIONS

    esperado = {e[1:] for e in BOOK_EXTENSIONS}
    assert _extensoes(RAIZ / "lib" / "comum.sh", "PRELO_EXTENSOES=") == esperado
    assert _extensoes(RAIZ / "windows" / "bin" / "scripts" / "comum.ps1", "$PreloExtensoes =") == esperado


def test_scripts_do_windows_legiveis_pelo_powershell_5():
    # sem BOM, o Windows PowerShell 5.1 lê os .ps1 como ANSI e estraga os acentos;
    # sem CRLF, o cmd.exe se perde em atalhos .cmd
    scripts = list((RAIZ / "windows").rglob("*.ps1"))
    assert len(scripts) >= 5  # build.ps1, comum.ps1 e os três utilitários
    for ps1 in scripts:
        assert ps1.read_bytes().startswith(b"\xef\xbb\xbf"), f"{ps1.name} sem BOM UTF-8"
    for cmd in (RAIZ / "windows" / "bin").glob("*.cmd"):
        dados = cmd.read_bytes()
        assert dados.count(b"\n") == dados.count(b"\r\n"), f"{cmd.name} sem CRLF"
        assert dados.isascii(), f"{cmd.name} com caracteres fora do ASCII"
