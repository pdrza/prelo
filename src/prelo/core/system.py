"""Integração com o sistema operacional: Linux (freedesktop) e Windows.

Tudo o que muda de um sistema para o outro fica aqui, para o resto do Prelo não
precisar saber onde está rodando:

* onde ficam os dados (``~/.local/share/prelo`` ou ``%LOCALAPPDATA%\\Prelo``);
* abrir um arquivo ou link no programa padrão e mostrar um arquivo na pasta;
* trava de instância única (``flock`` no Linux, ``msvcrt.locking`` no Windows);
* troca atômica de arquivos que tolera o antivírus do Windows segurando o arquivo;
* programas auxiliares sem janela de console no Windows;
* identidade da janela na barra de tarefas e o WebView2 do Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from .. import APP_ID, APP_NAME

IS_WINDOWS = sys.platform == "win32"

# O mesmo do atalho criado pelo instalador: a janela se agrupa com ele na barra de
# tarefas (e pode ser fixada), em vez de aparecer como "Python".
APP_USER_MODEL_ID = "Prelo.Prelo"

# Instalador do WebView2 Runtime da Microsoft (a janela do Prelo no Windows usa o
# motor do Edge; vem com o Windows 11 e chega ao Windows 10 pelo Windows Update).
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


# ------------------------------------------------------------------ pastas
def user_data_dir() -> Path:
    """Pasta de dados padrão do usuário (sem considerar ``PRELO_DATA_DIR``)."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_ID


def runtime_dir() -> Path:
    """Pasta da trava e do endereço da instância aberta, só do usuário atual."""
    if IS_WINDOWS:
        base = tempfile.gettempdir()  # %TEMP% já fica no perfil do usuário
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{APP_ID}-{os.getuid()}"
    path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


# --------------------------------------------------------------- arquivos
def replace_file(src: str | os.PathLike, dst: str | os.PathLike, *, attempts: int = 10) -> None:
    """``os.replace`` que, no Windows, espera o antivírus ou o indexador soltarem o arquivo.

    O Windows recusa trocar um arquivo que outro programa está lendo naquele
    instante (o Defender examina cada arquivo novo); a tentativa seguinte, uma
    fração de segundo depois, costuma passar. No Linux o erro sobe na hora.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not IS_WINDOWS or attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def path_from_file_uri(uri: str) -> str:
    """``file:///C:/Livros/a%20b.pdf`` → ``C:\\Livros\\a b.pdf``; ``file:///home/...`` no Linux."""
    parsed = urlparse(uri)
    path = url2pathname(parsed.path)
    if IS_WINDOWS and parsed.netloc and parsed.netloc.lower() != "localhost":
        path = f"\\\\{parsed.netloc}{path}"  # compartilhamento de rede: \\servidor\pasta\livro.pdf
    return path


class InstanceLock:
    """Trava exclusiva num arquivo; o sistema a solta sozinho se o processo morrer."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        fh = open(self.path, "a+")  # noqa: SIM115 - fica aberto enquanto a trava valer
        try:
            if IS_WINDOWS:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # outra instância segura a trava
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


# ---------------------------------------------------------------- programas
def no_window_flags() -> int:
    """``creationflags`` para rodar um programa de console sem abrir janela preta no Windows."""
    return subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def open_path(target: str) -> None:
    """Abre um arquivo, uma pasta ou um link no programa padrão do sistema."""
    if IS_WINDOWS:
        os.startfile(target)  # noqa: S606
        return
    subprocess.Popen(["gio", "open", target] if shutil.which("gio") else ["xdg-open", target],  # noqa: S603
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal_in_file_manager(path: str) -> None:
    """Abre a pasta com o arquivo selecionado.

    Linux: interface D-Bus padrão do freedesktop (Nautilus, Dolphin, Nemo...); sem
    ela, só abre a pasta. Windows: Explorador de Arquivos com o arquivo selecionado.
    """
    p = Path(path).resolve()
    if IS_WINDOWS:
        if p.is_file():
            # linha montada à mão: o explorer.exe não segue as regras de aspas do C
            subprocess.Popen(f'explorer.exe /select,"{p}"')  # noqa: S603,S607
        else:
            os.startfile(p)  # noqa: S606
        return
    if shutil.which("gdbus"):
        proc = subprocess.run(  # noqa: S603,S607
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowItems", f"['{p.as_uri()}']", ""],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if proc.returncode == 0:
            return
    folder = str(p.parent if p.is_file() else p)
    subprocess.Popen(["gio", "open", folder] if shutil.which("gio") else ["xdg-open", folder],  # noqa: S603
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_error(text: str) -> None:
    """Mostra um erro de partida quando ainda não há janela (e talvez nem terminal)."""
    if IS_WINDOWS:
        import ctypes

        MB_ICONERROR, MB_SETFOREGROUND = 0x10, 0x10000
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, MB_ICONERROR | MB_SETFOREGROUND)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", APP_NAME, "-u", "critical", APP_NAME, text],  # noqa: S603,S607
                       check=False)


# ------------------------------------------------------------------ janela
def set_app_user_model_id() -> None:
    """Windows: a janela se agrupa com o atalho do Prelo na barra de tarefas, não com o Python."""
    if not IS_WINDOWS:
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def webview2_version() -> str | None:
    """Versão do Microsoft Edge WebView2 Runtime instalada (Windows), ou None.

    Sem ele, o pywebview cairia no motor do Internet Explorer, que não roda a
    interface (módulos JavaScript, WebGL): melhor abrir no navegador.
    """
    if not IS_WINDOWS:
        return None
    import winreg

    clients = r"Microsoft\EdgeUpdate\Clients"
    products = ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",   # WebView2 Runtime
                "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",   # Edge Beta,
                "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",   # Dev
                "{65C35B14-6C1D-4122-AC46-7148CC9D6497}")   # e Canary também servem
    places = ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{clients}"),
              (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{clients}"),
              (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{clients}"))
    for root, base in places:
        for product in products:
            try:
                with winreg.OpenKey(root, rf"{base}\{product}") as key:
                    version = str(winreg.QueryValueEx(key, "pv")[0])
            except OSError:
                continue
            if version and version != "0.0.0.0":  # "0.0.0.0" fica para trás quando é desinstalado
                return version
    return None
