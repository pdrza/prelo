"""Integração com o sistema operacional (``prelo.core.system``): Linux e Windows."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from prelo.core.system import IS_WINDOWS, InstanceLock, path_from_file_uri, replace_file

so_windows = pytest.mark.skipif(not IS_WINDOWS, reason="só no Windows")


def test_trava_de_instancia_unica(tmp_path):
    a, b = InstanceLock(tmp_path / "app.lock"), InstanceLock(tmp_path / "app.lock")
    assert a.acquire()
    assert not b.acquire()  # outra instância não consegue
    a.release()
    assert b.acquire()  # solta, a próxima pega
    b.release()


def test_link_de_arquivo_vira_caminho():
    if IS_WINDOWS:
        assert path_from_file_uri("file:///C:/Livros/Meu%20livro%20%C3%A1.pdf") == r"C:\Livros\Meu livro á.pdf"
        assert path_from_file_uri("file://servidor/pasta/x.epub") == r"\\servidor\pasta\x.epub"
    else:
        assert path_from_file_uri("file:///home/ana/Meu%20livro%20%C3%A1.pdf") == "/home/ana/Meu livro á.pdf"


def test_pasta_de_dados(monkeypatch, tmp_path):
    from prelo.core.storage import data_dir

    monkeypatch.delenv("PRELO_DATA_DIR", raising=False)
    if IS_WINDOWS:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert data_dir() == tmp_path / "Prelo"
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert data_dir() == tmp_path / "prelo"


def test_filtros_do_seletor_valem_para_o_pywebview():
    webview_util = pytest.importorskip("webview.util")
    from prelo.app.desktop import book_filters

    filtros = book_filters()
    for filtro in filtros:
        webview_util.parse_file_type(filtro)  # ValueError se o pywebview recusar
    assert any("*.PDF" in f for f in filtros) != IS_WINDOWS  # o GTK diferencia maiúsculas; o Windows, não


@so_windows
def test_troca_espera_o_arquivo_ser_solto(tmp_path):
    """O Windows recusa trocar um arquivo aberto por outro programa (o antivírus abre
    cada arquivo novo por um instante); a troca tenta de novo em vez de falhar."""
    destino, novo = tmp_path / "library.json", tmp_path / "novo.json"
    destino.write_text("velho")
    novo.write_text("novo")
    with open(destino, "rb"), pytest.raises(PermissionError):
        os.replace(novo, destino)  # sem esperar, o Windows recusa
    aberto = open(destino, "rb")  # noqa: SIM115 - fechado pelo temporizador
    threading.Timer(0.3, aberto.close).start()
    replace_file(novo, destino)
    assert destino.read_text() == "novo"


@so_windows
def test_pymupdf_sem_console(tmp_path):
    """Um app de janela no Windows (pythonw, .exe sem console) não tem saída de erro.
    Importar o PyMuPDF depois do Prelo não pode quebrar ("Identificador inválido")."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("pythonw.exe não encontrado")
    resultado = tmp_path / "resultado.txt"
    codigo = (
        "import os\n"
        "try:\n"
        "    import prelo, pymupdf\n"
        "    texto = 'ok ' + os.environ['PYMUPDF_MESSAGE']\n"
        "except BaseException as exc:\n"
        "    texto = repr(exc)\n"
        f"open(r'{resultado}', 'w', encoding='utf-8').write(texto)\n"
    )
    sem_console = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESTDHANDLES)  # sem entrada, saída nem erro
    ambiente = {k: v for k, v in os.environ.items() if k != "PYMUPDF_MESSAGE"}
    subprocess.run([str(pythonw), "-c", codigo], startupinfo=sem_console, env=ambiente, timeout=120, check=False)
    texto = resultado.read_text(encoding="utf-8")
    assert texto == "ok logging:name=pymupdf", texto
