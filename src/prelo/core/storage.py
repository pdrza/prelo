"""Onde o Prelo guarda dados: pasta do usuário, preferências e JSON atômico.

Estante, capas, preferências e ``prelo.log`` ficam em:

* Linux: ``$XDG_DATA_HOME/prelo`` (normalmente ``~/.local/share/prelo``);
* Windows: ``%LOCALAPPDATA%\\Prelo``.

``PRELO_DATA_DIR`` substitui o caminho (útil em testes).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .system import replace_file, user_data_dir


def data_dir() -> Path:
    env = os.environ.get("PRELO_DATA_DIR")
    path = Path(env) if env else user_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        replace_file(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


DEFAULT_SETTINGS = {
    "output_dir": "",          # vazio = ao lado do arquivo original
    "default_format": "azw3",
    "default_profile": "auto",
    "theme": "system",          # system | light | dark
    "reduce_motion": False,     # "modo desempenho": desliga animações
    "parallel_jobs": 1,
    "open_folder_when_done": False,
    "saved_defaults": {},       # opções padrão do usuário (nível LOW)
    "last_add_dir": "",         # última pasta do "Adicionar livros" (como o Calibre)
    "reader": {},               # leitor: tema, páginas lado a lado e letra (a janela não guarda localStorage)
}

READER_THEMES = ("claro", "papel", "sepia", "noite")
READER_SPREADS = ("auto", "1", "2")


def reader_prefs(value) -> dict:
    """Preferências do leitor, só com chaves e valores conhecidos."""
    value = value if isinstance(value, dict) else {}
    out: dict = {}
    if value.get("theme") in READER_THEMES:
        out["theme"] = value["theme"]
    if str(value.get("spread")) in READER_SPREADS:
        out["spread"] = str(value["spread"])
    try:
        size = int(value.get("font_size"))
    except (TypeError, ValueError):
        size = 0
    if 10 <= size <= 48:
        out["font_size"] = size
    return out


class Settings:
    def __init__(self, folder: Path):
        self.path = folder / "settings.json"
        self._lock = threading.Lock()
        stored = read_json(self.path, {})
        self.data = {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}

    def update(self, changes: dict) -> dict:
        from .options import OPTIONS, unknown_option

        with self._lock:
            for key, value in changes.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                if key == "saved_defaults":
                    clean = {}
                    for name, v in (value or {}).items():
                        if name not in OPTIONS:
                            raise unknown_option(name)
                        clean[name] = OPTIONS[name].coerce(v)
                    value = clean
                if key == "parallel_jobs":
                    value = max(1, min(4, int(value)))
                if key == "reader":
                    value = reader_prefs(value)
                self.data[key] = value
            write_json_atomic(self.path, self.data)
            return dict(self.data)
