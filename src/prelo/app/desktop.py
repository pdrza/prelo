"""Aplicativo de desktop para Linux e Windows.

Sobe a API em 127.0.0.1 numa porta livre, com token aleatório, e abre a
interface numa janela nativa (pywebview: GTK/WebKitGTK no Linux, WebView2 do
Edge no Windows; Qt se preferir: ``PRELO_GUI=qt``). Sem janela nativa, abre no
navegador padrão.

Integração com o desktop:

* **uma instância só**: abrir o Prelo de novo (ou "Abrir com Prelo" no
  gerenciador de arquivos) manda os arquivos para a janela que já está aberta;
* arquivos passados na linha de comando entram na estante e a janela abre neles;
* "Mostrar na pasta" seleciona o arquivo no gerenciador de arquivos.

O que muda de um sistema para o outro está em :mod:`prelo.core.system`.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import APP_ID, APP_NAME
from ..core.system import (
    IS_WINDOWS, WEBVIEW2_URL, InstanceLock, open_path, path_from_file_uri, reveal_in_file_manager,
    runtime_dir, set_app_user_model_id, show_error, webview2_version,
)

log = logging.getLogger("prelo.app")
MAX_DOWNLOAD = 512 * 1024 * 1024   # livro arrastado de um link: limite do download
ICON_ICO = Path(__file__).resolve().parent.parent / "ui" / "img" / "icon.ico"


class WindowUnavailable(RuntimeError):
    """Não dá para abrir a janela nativa; ``hint`` diz o que instalar."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def book_filters() -> tuple[str, ...]:
    """Filtros do seletor, como no "Adicionar livros" do Calibre: "Livros" com tudo o
    que o Prelo lê e um filtro por família. O GTK diferencia maiúsculas: vão as duas
    (o seletor do Windows não diferencia)."""
    from ..core.formats import BOOK_FILTERS

    out = []
    for name, exts in BOOK_FILTERS:
        patterns = [p for e in exts for p in ((f"*{e}",) if IS_WINDOWS else (f"*{e}", f"*{e.upper()}"))]
        out.append(f"{name} ({';'.join(patterns)})")
    return (*out, "Todos os arquivos (*.*)")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bridge:
    """Métodos expostos ao JavaScript como ``window.pywebview.api.*``."""

    def __init__(self, app):
        self._app = app
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def _dialog(self, kind: str):
        import webview

        dialogs = getattr(webview, "FileDialog", None)
        if dialogs is not None:
            return getattr(dialogs, kind)
        return {"OPEN": webview.OPEN_DIALOG, "FOLDER": webview.FOLDER_DIALOG}[kind]

    def choose_books(self) -> list[str]:
        settings = self._app.state.settings
        last = settings.data.get("last_add_dir") or ""
        start = last if last and Path(last).is_dir() else str(Path.home() / "Downloads" if (Path.home() / "Downloads").is_dir() else Path.home())
        result = self._window.create_file_dialog(self._dialog("OPEN"), directory=start, allow_multiple=True,
                                                 file_types=book_filters())
        paths = [str(p) for p in (result or [])]
        if paths:  # a próxima vez abre na mesma pasta (o Calibre lembra "add books dialog dir")
            settings.update({"last_add_dir": str(Path(paths[0]).parent)})
        return paths

    def choose_folder(self) -> str | None:
        result = self._window.create_file_dialog(self._dialog("FOLDER"))
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    def reveal(self, path: str) -> bool:
        if Path(path).exists():
            reveal_in_file_manager(path)
            return True
        return False

    def open_file(self, path: str) -> bool:
        if not Path(path).exists():
            return False
        open_path(path)
        return True

    def quit(self) -> None:
        self._window.destroy()

    def notify_added(self, added: list[dict], errors: list[dict]) -> None:
        payload = json.dumps({"added": added, "errors": errors}, ensure_ascii=False)
        self._window.evaluate_js(f"window.prelo && window.prelo.booksAdded({payload})")
        try:
            self._window.restore()
            self._window.show()
        except Exception:  # noqa: BLE001 - nem todo backend traz a janela para a frente
            pass

    def _fresh(self, paths: list[str]) -> list[str]:
        """O mesmo arquivo pode chegar pelos dois caminhos do arrastar (evento do GTK
        e URL lida pela página): o segundo, logo em seguida, é ignorado."""
        now, out = time.monotonic(), []
        recent = self.__dict__.setdefault("_recent", {})
        for p in paths:
            key = str(Path(p).expanduser().resolve())
            if now - recent.get(key, -99) > 5:
                out.append(p)
            recent[key] = now
        return out

    def add_dropped(self, paths: list[str]) -> None:
        """Arquivos soltos na janela, com o caminho completo vindo do GTK."""
        self._window.evaluate_js("window.prelo && window.prelo.dropHandled && window.prelo.dropHandled()")
        paths = self._fresh(paths)
        if paths:
            added, errors = add_paths(self._app.state.library, paths)
            self.notify_added(added, errors)

    def add_uris(self, uris: list[str]) -> dict:
        """Links soltos na janela: ``file://`` (gerenciador de arquivos, painel de downloads
        do navegador) ou ``http(s)://``, que é baixado antes, como faz o Calibre."""
        library = self._app.state.library
        paths, errors = [], []
        for uri in uris[:50]:
            parsed = urllib.parse.urlparse(uri.strip())
            if parsed.scheme == "file":
                paths.append(path_from_file_uri(uri.strip()))
            elif parsed.scheme in ("http", "https"):
                try:
                    paths.append(str(download_book(uri, library.uploads)))
                except Exception as exc:  # noqa: BLE001
                    errors.append({"path": uri, "message": getattr(exc, "message", str(exc))})
        added, more = add_paths(library, self._fresh(paths))
        return {"added": added, "errors": errors + more}


def download_book(url: str, folder: Path) -> Path:
    """Baixa um livro arrastado como link (só http/https, com limite de tamanho)."""
    from ..core.errors import PreloError
    from ..core.formats import BOOK_EXTENSIONS
    from ..core.safety import sanitize_filename

    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - só http(s), conferido antes
        name = resp.headers.get_filename() or Path(urllib.parse.unquote(urllib.parse.urlparse(resp.url).path)).name
        stem, ext = Path(name or "livro").stem, Path(name or "").suffix.lower()
        if ext not in BOOK_EXTENSIONS:
            raise PreloError("O link não aponta para um livro.",
                             hint="Arraste o arquivo baixado ou um link direto para um PDF, EPUB, AZW3, MOBI ou CBZ.")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{sanitize_filename(stem)[:60]}-{secrets.token_hex(3)}{ext}"
        size = 0
        with open(dest, "wb") as out:
            while chunk := resp.read(1 << 20):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise PreloError("O arquivo do link passa de 512 MB.")
                out.write(chunk)
    return dest


def expand_paths(paths: list[str], limit: int = 2000) -> tuple[list[Path], list[dict]]:
    """Confere cada caminho como o ``validate_source`` do Calibre (existe? dá para ler?)
    e troca pastas pelos livros que há dentro delas."""
    from ..core.formats import BOOK_EXTENSIONS

    files, errors = [], []
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists():
            errors.append({"path": raw, "message": "O arquivo não existe (foi movido ou apagado?)."})
        elif not os.access(p, os.R_OK | (os.X_OK if p.is_dir() else 0)):
            errors.append({"path": raw, "message": "Sem permissão para ler este arquivo."})
        elif p.is_dir():
            found = sorted(f for f in p.rglob("*") if f.suffix.lower() in BOOK_EXTENSIONS and f.is_file()
                           and not any(part.startswith(".") for part in f.relative_to(p).parts))
            if not found:
                errors.append({"path": raw, "message": "Nenhum livro nesta pasta."})
            files += found
        else:
            files.append(p)
    return files[:limit], errors


def add_paths(library, paths: list[str]) -> tuple[list[dict], list[dict]]:
    files, errors = expand_paths(paths)
    added = []
    for p in files:
        try:
            added.append(library.add(p).to_dict())
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(p), "message": getattr(exc, "message", str(exc)),
                           "hint": getattr(exc, "hint", "")})
    return added, errors


def _register_drop(window, bridge: Bridge) -> None:
    try:
        from webview.dom import DOMEventHandler
    except ImportError:  # pywebview antigo: o arrastar cai no upload do navegador
        return

    def on_drop(event: dict) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        log.info("arrastar: %d arquivo(s), %d com caminho completo", len(files), len(paths))
        if paths:
            threading.Thread(target=bridge.add_dropped, args=(paths,), daemon=True).start()

    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


# ------------------------------------------------------------ instância única
class Instance:
    """Arquivo de trava + endereço da instância em :func:`runtime_dir`
    (``$XDG_RUNTIME_DIR/prelo`` no Linux, ``%TEMP%\\prelo`` no Windows)."""

    def __init__(self):
        folder = runtime_dir()
        self.lock_path = folder / "app.lock"
        self.info_path = folder / "app.json"
        self._lock = InstanceLock(self.lock_path)
        self._held = False

    def acquire(self) -> bool:
        self._held = self._lock.acquire()
        return self._held

    @property
    def held(self) -> bool:
        return self._held

    def publish(self, port: int, token: str) -> None:
        self.info_path.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}))
        self.info_path.chmod(0o600)

    def send_to_running(self, paths: list[str], wait: float = 10.0) -> bool:
        """Entrega os arquivos à janela que já está aberta (e a traz para a frente).

        A outra instância pode estar subindo ainda: no Windows, "Abrir com" em vários
        arquivos abre um processo por arquivo. Espera ela publicar o endereço."""
        body = json.dumps({"paths": [str(Path(p).resolve()) for p in paths]}).encode()
        deadline = time.monotonic() + wait
        while True:
            try:
                info = json.loads(self.info_path.read_text())
                req = urllib.request.Request(
                    f"http://127.0.0.1:{info['port']}/api/desktop/open", data=body,
                    headers={"Content-Type": "application/json", "X-Prelo-Token": info["token"]})
                with urllib.request.urlopen(req, timeout=60):  # noqa: S310 - só localhost
                    return True
            except Exception:  # noqa: BLE001
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.25)

    def release(self) -> None:
        if self._held:
            self.info_path.unlink(missing_ok=True)
            self._lock.release()
            self._held = False


# ------------------------------------------------------------ partida
def _setup_logging() -> str:
    from ..core.storage import data_dir

    log_path = str(data_dir() / "prelo.log")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None and sys.stderr.isatty():
        root.addHandler(logging.StreamHandler())
    return log_path


def _notify_error(text: str) -> None:
    log.error(text)
    try:
        show_error(text)
    except Exception:  # noqa: BLE001 - o aviso é cortesia; o log já tem o erro
        log.debug("aviso de erro não exibido", exc_info=True)


DIAGNOSTICO_JS = """(() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
  return JSON.stringify({
    interface: Boolean(window.prelo), janela: document.querySelector('#aw-title')?.textContent || '',
    icones: document.querySelectorAll('.desk-icon').length,
    carrossel: Boolean(document.querySelector('.shelf canvas')),
    placas: Number(document.querySelector('.shelf')?.dataset.placas || 0),
    webgl: gl ? (dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'sim') : 'indisponível',
    navegador: navigator.userAgent,
  });
})()"""


def _diagnose(window) -> None:
    """--diagnostico: confere se a interface carregou e fecha a janela."""
    time.sleep(4)
    try:
        report = json.loads(window.evaluate_js(DIAGNOSTICO_JS))
    except Exception as exc:  # noqa: BLE001
        report = {"erro": repr(exc)}
    ok = bool(report.get("interface")) and report.get("icones", 0) > 0
    report["resultado"] = "ok" if ok else "falhou"
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    log.info("diagnóstico: %s", report)
    window.destroy()


def _start_window(url: str, bridge: Bridge, diagnose: bool = False) -> None:
    import webview

    gui = (os.environ.get("PRELO_GUI") or "").lower() or None
    extra: dict = {}
    if IS_WINDOWS:
        if gui in (None, "edgechromium") and not webview2_version():
            raise WindowUnavailable("o Microsoft Edge WebView2 Runtime não está instalado",
                                    hint=f"Instale o WebView2 Runtime ({WEBVIEW2_URL}) e abra o Prelo de novo.")
        set_app_user_model_id()  # agrupa com o atalho do Prelo na barra de tarefas
        if ICON_ICO.is_file():
            extra["icon"] = str(ICON_ICO)  # ícone da janela (sem ele, o do Python)
    elif gui in (None, "gtk"):
        try:  # nome do programa = "prelo": o ícone do atalho agrupa a janela certo
            from gi.repository import GLib

            GLib.set_prgname(APP_ID)
            GLib.set_application_name(APP_NAME)
        except Exception:  # noqa: BLE001
            pass
    window = webview.create_window(
        APP_NAME, url, js_api=bridge, width=1320, height=860, min_size=(980, 660),
        background_color="#FAFAFA", text_select=False,
    )
    bridge.attach(window)
    window.events.loaded += lambda: _register_drop(window, bridge)
    if diagnose:
        window.events.loaded += lambda: threading.Thread(target=_diagnose, args=(window,), daemon=True).start()
    webview.start(gui=gui, debug=os.environ.get("PRELO_DEBUG") == "1", **extra)


def _window_hint(exc: Exception) -> str:
    """O que fazer para ter a janela nativa, conforme o sistema."""
    if isinstance(exc, WindowUnavailable) and exc.hint:
        return exc.hint
    if IS_WINDOWS:
        return ("Reinstale o Prelo pelo instalador para Windows, ou rode 'pip install pywebview' no "
                "ambiente do Prelo.")
    return ("Para ter a janela, rode ./install.sh e abra o Prelo pelo menu (ou saia do ambiente "
            "virtual com 'deactivate').")


def run(paths: list[str], diagnose: bool = False) -> int:
    import uvicorn

    from ..api.server import create_app

    instance = Instance()
    if not instance.acquire() and not diagnose:
        if instance.send_to_running(paths):
            return 0
        log.warning("Outra instância parece travada; abrindo mesmo assim.")

    port = free_port()
    token = secrets.token_urlsafe(24)
    bridge = None
    no_navegador = False
    server_box: dict = {}

    def on_open(added: list[dict], errors: list[dict]) -> None:
        """Outro "prelo-app" entregou arquivos (ou só pediu para aparecer)."""
        if bridge is not None and bridge._window is not None:
            bridge.notify_added(added, errors)
        elif no_navegador:  # sem janela: abre a interface de novo no navegador
            frag = f"#view=estante&book={added[0]['id']}" if added else ""
            open_path(f"http://127.0.0.1:{port}/?token={token}{frag}")

    app = create_app(token=token, mode="desktop", on_open=on_open,
                     on_quit=lambda: setattr(server_box["server"], "should_exit", True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           log_config=None))
    server_box["server"] = server
    thread = threading.Thread(target=server.run, name="prelo-api", daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        _notify_error("A API local do Prelo não iniciou. Veja o prelo.log.")
        return 2
    # o diagnóstico, rodando ao lado de uma janela aberta, não toma o endereço dela
    # (uma instância travada, sim: as próximas aberturas chegam à janela que funciona)
    if instance.held or not diagnose:
        instance.publish(port, token)

    fragment = ""
    if paths:
        added, errors = add_paths(app.state.library, paths)
        for e in errors:
            log.warning("Não foi possível adicionar %s: %s", e["path"], e["message"])
        if added:
            fragment = f"#view=estante&book={added[0]['id']}"
    url = f"http://127.0.0.1:{port}/?token={token}{fragment}"
    log.info("API em http://127.0.0.1:%s", port)

    bridge = Bridge(app)
    try:
        _start_window(url, bridge, diagnose)
    except Exception as exc:  # noqa: BLE001 - sem GTK/Qt/WebView2: usa o navegador padrão
        if isinstance(exc, ImportError):
            motivo = f"falta o módulo '{exc.name}'"
        elif isinstance(exc, WindowUnavailable):
            motivo = str(exc)
        else:
            motivo = f"{type(exc).__name__}: {exc}"
        log.warning("Janela nativa indisponível neste Python (%s): %s. %s Abrindo no navegador.",
                    sys.executable, motivo, _window_hint(exc))
        log.debug("detalhes", exc_info=True)
        if diagnose:
            print(json.dumps({"resultado": "falhou", "erro": f"janela nativa indisponível: {motivo}",
                              "dica": _window_hint(exc), "python": sys.executable}, ensure_ascii=False))
            return 1
        bridge = None
        no_navegador = True
        open_path(url)
        while thread.is_alive():  # até "Prelo › Sair" chamar /api/quit
            thread.join(0.5)
    finally:
        app.state.jobs.shutdown()
        server.should_exit = True
        thread.join(3)
        instance.release()
    return 0


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    log_path = _setup_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [a for a in args if not a.startswith("-")]
    try:
        return run(paths, diagnose="--diagnostico" in args)
    except Exception:  # noqa: BLE001
        log.exception("Falha ao iniciar")
        _notify_error(f"O Prelo não conseguiu iniciar. Detalhes em {log_path}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
