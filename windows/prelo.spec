# PyInstaller: o Prelo para Windows, sem precisar de Python instalado.
#
#   pyinstaller windows/prelo.spec      (o windows/build.ps1 faz tudo, inclusive o instalador)
#
# Gera uma pasta com dois executáveis que dividem as mesmas bibliotecas:
#
#   prelo-app.exe   a janela (sem console): o que o atalho do menu Iniciar abre
#   prelo.exe       a linha de comando (prelo convert, prelo serve...), usada também
#                   pelos utilitários prelo-lote, prelo-enviar e prelo-vigiar
#
# Os nomes seguem os comandos do Linux. "Prelo.exe" e "prelo.exe" seriam o mesmo
# arquivo no Windows, que não diferencia maiúsculas.
# ruff: noqa: F821 - Analysis, PYZ, EXE, COLLECT e SPECPATH vêm do PyInstaller

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

RAIZ = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(RAIZ / "src"))
from prelo import APP_NAME, __version__  # noqa: E402

ICONE = str(RAIZ / "src" / "prelo" / "ui" / "img" / "icon.ico")
ENTRADAS = RAIZ / "windows" / "pyinstaller"

# Imports feitos dentro de funções (leitores e escritores de cada formato) e o
# trabalho que roda em outro processo: tudo do pacote vai junto.
OCULTOS = collect_submodules("prelo")
FORA = [  # bibliotecas que o Prelo não usa no Windows (algumas são puxadas por dependências)
    "tkinter", "_tkinter", "gi", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "cefpython3",
    "numpy", "pytest", "IPython", "matplotlib", "webview.platforms.gtk", "webview.platforms.qt",
    "webview.platforms.cocoa", "webview.platforms.android", "webview.platforms.cef",
]
DADOS = [(str(RAIZ / "src" / "prelo" / "ui"), "prelo/ui")]  # a interface (HTML, CSS, JS, fontes, ícones)


def versao(descricao: str, arquivo: str) -> VSVersionInfo:
    """Propriedades do executável (Explorador › Propriedades › Detalhes e Gerenciador de Tarefas)."""
    numeros = tuple(int(p) for p in (__version__.split(".") + ["0", "0", "0"])[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeros, prodvers=numeros),
        kids=[
            StringFileInfo([StringTable("041604B0", [  # português (Brasil), Unicode
                StringStruct("CompanyName", APP_NAME),
                StringStruct("FileDescription", descricao),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", arquivo),
                StringStruct("LegalCopyright", "AGPL-3.0-or-later"),
                StringStruct("OriginalFilename", f"{arquivo}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0416, 1200])]),
        ],
    )


def analise(script: str) -> Analysis:
    return Analysis([str(ENTRADAS / script)], pathex=[str(RAIZ / "src")], datas=DADOS,
                    hiddenimports=OCULTOS, excludes=FORA, noarchive=False, optimize=0)


janela = analise("prelo_app.py")
linha = analise("prelo_cli.py")

exe_janela = EXE(
    PYZ(janela.pure), janela.scripts, [], exclude_binaries=True, name="prelo-app",
    icon=ICONE, version=versao(f"{APP_NAME}: conversor de livros", "prelo-app"),
    console=False, upx=False,  # sem UPX: executáveis comprimidos assustam antivírus à toa
)
exe_linha = EXE(
    PYZ(linha.pure), linha.scripts, [], exclude_binaries=True, name="prelo",
    icon=ICONE, version=versao(f"{APP_NAME}: linha de comando", "prelo"),
    console=True, upx=False,
)
COLLECT(exe_janela, janela.binaries, janela.datas, exe_linha, linha.binaries, linha.datas,
        upx=False, name="Prelo")
