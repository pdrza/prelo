"""Pré-visualização de páginas com ajustes (para o editor de página).

Documentos abertos ficam num pequeno cache; o MuPDF não é seguro entre
threads (nem com documentos diferentes), então todo uso dele no processo da API
passa pela mesma trava, :data:`MUPDF_LOCK`: prévia, leitor e estante.

Livros Kindle chegam aqui como o EPUB que o leitor fez deles
(:meth:`prelo.core.reader.Reader.source`).
"""

from __future__ import annotations

import io
import threading
import zipfile
from collections import OrderedDict

from PIL import Image

from .formats import comic_pages
from .imaging import Adjustments, apply_adjustments, flatten

MAX_WIDTH = 1800
MUPDF_LOCK = threading.RLock()  # uma chamada ao MuPDF por vez no processo


class PreviewRenderer:
    def __init__(self, cache_size: int = 4):
        self._lock = MUPDF_LOCK
        self._docs: OrderedDict[str, tuple] = OrderedDict()
        self._size = cache_size

    def _open(self, book_id: str, path: str, fmt: str):
        key = f"{book_id}:{path}"
        if key in self._docs:
            self._docs.move_to_end(key)
            return self._docs[key]
        if fmt in ("pdf", "epub"):
            import pymupdf

            doc = pymupdf.open(path, filetype=fmt)
            if fmt == "epub":
                doc.layout(width=420, height=595, fontsize=11)
            item = ("doc", doc)
        elif fmt == "comic":
            with zipfile.ZipFile(path) as zf:
                item = ("zip", comic_pages(zf.namelist()))
        elif fmt == "image":
            item = ("image", None)
        else:
            raise ValueError(f"a prévia não abre o formato {fmt!r} direto")
        self._docs[key] = item
        while len(self._docs) > self._size:
            self._release(self._docs.popitem(last=False)[1])
        return item

    @staticmethod
    def _release(item: tuple) -> None:
        if item[0] == "doc":
            item[1].close()

    def page_count(self, book_id: str, path: str, fmt: str) -> int:
        with self._lock:
            kind, obj = self._open(book_id, path, fmt)
            if kind == "doc":
                return obj.page_count
            if kind == "zip":
                return len(obj)
            return 1

    def render(self, book_id: str, path: str, fmt: str, index: int, width: int,
               adj: Adjustments | None) -> bytes:
        width = max(120, min(MAX_WIDTH, int(width)))
        with self._lock:
            kind, obj = self._open(book_id, path, fmt)
            if kind == "doc":
                import pymupdf

                index = max(0, min(obj.page_count - 1, index))
                page = obj[index]
                scale = width / max(1.0, page.rect.width)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            elif kind == "zip":
                index = max(0, min(len(obj) - 1, index))
                with zipfile.ZipFile(path) as zf, Image.open(io.BytesIO(zf.read(obj[index]))) as im:
                    im.load()
                    img = flatten(im)
            else:
                with Image.open(path) as im:
                    im.load()
                    img = flatten(im)
        if img.width > width:
            img.thumbnail((width, int(width * img.height / img.width)), Image.Resampling.LANCZOS)
        if adj is not None:
            img = apply_adjustments(img, adj)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=88)
        return buf.getvalue()

    def forget(self, book_id: str) -> None:
        """Fecha os documentos abertos de um livro (antes de apagar o arquivo)."""
        with self._lock:
            for key in [k for k in self._docs if k.startswith(f"{book_id}:")]:
                self._release(self._docs.pop(key))

    def close(self) -> None:
        with self._lock:
            for item in self._docs.values():
                self._release(item)
            self._docs.clear()
