"""Motor alternativo: usa o ``ebook-convert`` do Calibre, se estiver instalado.

O padrão é o gerador nativo do Prelo. O Calibre é opcional (escolha em
Preferências › Motor AZW3/MOBI) e útil como segunda opinião.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core.errors import ConversionError
from ..core.model import Book
from ..core.system import IS_WINDOWS, no_window_flags
from .epub import write_epub

if IS_WINDOWS:  # instalador do calibre.com (64 e 32 bits), caso a pasta não esteja no PATH
    EXTRA_PATHS = [str(Path(os.environ[v]) / "Calibre2" / "ebook-convert.exe")
                   for v in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
else:  # instalação oficial do calibre.com (sem pacote da distribuição)
    EXTRA_PATHS = ["/opt/calibre/ebook-convert"]


def calibre_executable() -> str | None:
    exe = shutil.which("ebook-convert")
    if exe:
        return exe
    for p in EXTRA_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def write_with_calibre(book: Book, dest: Path, fmt: str, log=print) -> None:
    exe = calibre_executable()
    if not exe:
        raise ConversionError("O Calibre não está instalado.", hint="Use o motor nativo ou instale o Calibre.")
    with tempfile.TemporaryDirectory(prefix="prelo-calibre-") as tmp:
        epub = Path(tmp) / "entrada.epub"
        write_epub(book, epub)
        out = Path(tmp) / f"saida.{fmt}"
        cmd = [exe, str(epub), str(out)]
        if fmt == "mobi":
            cmd += ["--mobi-file-type", "both"]
        log("Calibre: " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=1800,  # noqa: S603
                              creationflags=no_window_flags())
        if proc.returncode != 0 or not out.exists():
            raise ConversionError("O Calibre não conseguiu converter o livro.",
                                  hint=(proc.stderr or proc.stdout)[-400:])
        shutil.copyfile(out, dest)
