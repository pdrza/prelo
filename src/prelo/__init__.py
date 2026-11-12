"""Prelo: conversor universal de livros que preserva o layout.

O pacote é dividido em:

- ``prelo.core``     modelo intermediário do livro, opções, perfis, segurança, imagem e texto
- ``prelo.inputs``   leitores (PDF, EPUB, Kindle, quadrinhos/imagens) que geram o livro intermediário
- ``prelo.outputs``  escritores (EPUB, AZW3, MOBI, CBZ, PDF, TXT) que partem do livro intermediário
- ``prelo.api``      API HTTP local (FastAPI) e fila de conversões
- ``prelo.app``      aplicativo de desktop (pywebview) que usa a API
"""

import os as _os

__version__ = "0.3.0"
APP_NAME = "Prelo"
APP_ID = "prelo"

# O PyMuPDF escreve avisos na saída padrão, que no Prelo é dos scripts (prelo convert --print-path).
# Vale também para os processos de conversão, que herdam o ambiente.
_os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")  # "Consider using the pymupdf_layout package..."
_os.environ.setdefault("PYMUPDF_MESSAGE", "fd:2")                # avisos do MuPDF vão para a saída de erro


def _fd_ok(fd: int) -> bool:
    try:
        _os.fstat(fd)
    except OSError:
        return False
    return True


# Um app de janela no Windows não tem saída de erro: o PyMuPDF falharia ao abri-la já
# no import ("Identificador inválido"). Ali os avisos vão para o log do Prelo.
if _os.environ["PYMUPDF_MESSAGE"] == "fd:2" and not _fd_ok(2):
    _os.environ["PYMUPDF_MESSAGE"] = "logging:name=pymupdf"
