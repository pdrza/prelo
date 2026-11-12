"""Leitor: as páginas de qualquer livro da estante, prontas para ler na janela.

Todo formato que o Prelo aceita é lido pelo mesmo motor da prévia e do layout
fiel (o MuPDF), de um destes dois jeitos:

* **fluido** (EPUB e livros Kindle): o texto é paginado de novo para o tamanho da
  página na janela e a letra escolhida;
* **fixo** (PDF, quadrinhos, imagens e EPUB de layout fixo): cada página mantém a
  diagramação do original e só muda de escala.

Livros Kindle (AZW3, MOBI, AZW, PRC) viram um EPUB pelo próprio Prelo na primeira
leitura. O EPUB fica em ``leitura/`` na pasta de dados, e as próximas aberturas
são imediatas.

**Onde parou.** Num livro fluido, o número da página muda com a letra e com o
tamanho da janela. O marcador do MuPDF também não serve: ele vale só enquanto o
arquivo está aberto. A posição guardada é o capítulo e quantas letras (sem contar
espaços) vêm antes do começo da página. Ela sobrevive à troca da letra, ao
redimensionar a janela e a fechar o app.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .errors import PreloError
from .formats import comic_pages
from .imaging import Adjustments, apply_adjustments, flatten
from .preview import MUPDF_LOCK
from .system import replace_file

MAX_SIDE = 4096  # maior lado de uma página renderizada, em pixels
FIXED_EPUB_SIZE = (1236, 1648)  # EPUB de layout fixo sem tamanho declarado: tela de um Kindle


def epub_shape(path: Path) -> tuple[bool, bool, tuple[int, int] | None]:
    """(layout fixo?, da direita para a esquerda?, tamanho das páginas do layout fixo)."""
    from lxml import etree

    from ..inputs.epub import NS, XML_PARSER, is_fixed_layout

    try:
        with zipfile.ZipFile(path) as zf:
            container = etree.fromstring(zf.read("META-INF/container.xml"), XML_PARSER)
            opf_path = container.find(".//c:rootfile", NS).get("full-path")
            raw = zf.read(opf_path)
            opf = etree.fromstring(raw, XML_PARSER)
            spine = opf.find(".//opf:spine", NS)
            rtl = spine is not None and spine.get("page-progression-direction") == "rtl"
            fixed = is_fixed_layout(opf)
            size = None
            if fixed:
                found = re.search(rb'original-resolution"\s+content="(\d+)x(\d+)"', raw)
                if found:
                    size = (int(found.group(1)), int(found.group(2)))
                elif spine is not None and len(spine):
                    size = _first_viewport(zf, opf, opf_path, spine)
    except Exception:  # noqa: BLE001 - EPUB estranho: lido como fluido, da esquerda para a direita
        return False, False, None
    return fixed, rtl, size


def _first_viewport(zf: zipfile.ZipFile, opf, opf_path: str, spine) -> tuple[int, int] | None:
    from urllib.parse import unquote

    from ..inputs.epub import NS

    items = {i.get("id"): i.get("href") for i in opf.iterfind(".//opf:manifest/opf:item", NS)}
    href = items.get(spine[0].get("idref"))
    if not href:
        return None
    base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    head = zf.read(base + unquote(href))[:4096]
    meta = re.search(rb"<meta[^>]*viewport[^>]*>", head, re.I)
    w = re.search(rb"width\s*=\s*(\d+)", meta.group(0)) if meta else None
    h = re.search(rb"height\s*=\s*(\d+)", meta.group(0)) if meta else None
    return (int(w.group(1)), int(h.group(1))) if w and h else None


@dataclass
class OpenBook:
    """Um livro aberto para leitura."""

    kind: str                                   # "reflow" (fluido) ou "fixed" (fixo)
    doc: object = None                          # pymupdf.Document, para PDF e EPUB
    zip_path: Path | None = None                # quadrinho: o ZIP e as imagens, em ordem
    images: list[str] = field(default_factory=list)
    image_path: Path | None = None              # uma imagem só
    ratios: list[float] = field(default_factory=list)  # largura/altura de cada página (livro fixo)
    rtl: bool = False                           # mangá e EPUB da direita para a esquerda
    layout: tuple[int, int, int] | None = None  # (largura, altura, letra) da paginação atual
    letters: dict[int, int] = field(default_factory=dict)  # letras por página, na paginação atual

    @property
    def pages(self) -> int:
        if self.doc is not None:
            return self.doc.page_count
        return len(self.images) if self.zip_path else 1

    def close(self) -> None:
        if self.doc is not None:
            self.doc.close()


class Reader:
    """Livros abertos para leitura (os últimos, num pequeno cache) e o que se faz com eles.

    As medidas de um livro fluido vêm em pixels CSS da janela: a página tem
    ``w`` × ``h`` e a letra ``fs`` pixels, e o MuPDF pagina nessas mesmas
    unidades. ``dpr`` é a densidade da tela: a imagem sai com ``w·dpr`` pixels
    de largura e fica nítida em qualquer escala do Windows ou do GNOME.
    """

    def __init__(self, folder: Path, cache_size: int = 3):
        self.cache = folder / "leitura"
        self._books: OrderedDict[str, OpenBook] = OrderedDict()
        self._size = cache_size
        self._preparing = threading.Lock()  # um livro Kindle convertido por vez

    # ------------------------------------------------------------ consultas
    def info(self, entry, w: int, h: int, fs: int) -> dict:
        """Tipo, páginas, sumário e onde começar a ler (o lugar onde parou)."""
        source = self.source(entry)
        with MUPDF_LOCK:
            book = self._open(entry, source)
            self._paginate(book, w, h, fs)
            return {
                "kind": book.kind,
                "pages": book.pages,
                "rtl": book.rtl,
                "ratios": [round(r, 4) for r in book.ratios] if book.kind == "fixed" else [],
                "toc": self._toc(book),
                "page": self._resume(book, entry.reading or {}) + 1,
            }

    def render(self, entry, page: int, w: int, h: int, fs: int, dpr: float,
               adj: Adjustments | None = None) -> bytes:
        """A página (contada de 1) em JPEG. Num livro fixo, ``w`` × ``h`` é a caixa onde ela cabe."""
        source = self.source(entry)
        with MUPDF_LOCK:
            book = self._open(entry, source)
            self._paginate(book, w, h, fs)
            img = self._image(book, self._clamp(book, page - 1), w, h, dpr)
        if adj is not None:
            img = apply_adjustments(img, adj)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        # texto: sem subamostragem de cor, para as letras não ganharem franjas
        img.save(buf, "JPEG", quality=90, subsampling=0 if book.kind == "reflow" else 2)
        return buf.getvalue()

    def links(self, entry, page: int, w: int, h: int, fs: int) -> list[dict]:
        """Links internos da página (sumário do livro, notas): área em frações da página e destino."""
        source = self.source(entry)
        with MUPDF_LOCK:
            book = self._open(entry, source)
            if book.doc is None:
                return []
            self._paginate(book, w, h, fs)
            pg = book.doc[self._clamp(book, page - 1)]
            box = pg.rect
            out = []
            for link in pg.get_links():
                target = link.get("page", -1)
                if target is None or target < 0 or "uri" in link:
                    continue
                area = link["from"]
                out.append({"x": round(area.x0 / box.width, 4), "y": round(area.y0 / box.height, 4),
                            "w": round(area.width / box.width, 4), "h": round(area.height / box.height, 4),
                            "page": int(target) + 1})
            return out

    def position(self, entry, page: int, w: int, h: int, fs: int) -> dict:
        """Onde o leitor está, num formato que sobrevive a outra letra e a reabrir o livro."""
        source = self.source(entry)
        with MUPDF_LOCK:
            book = self._open(entry, source)
            self._paginate(book, w, h, fs)
            index = self._clamp(book, page - 1)
            state = {"page": index + 1, "pages": book.pages,
                     "fraction": round(index / max(1, book.pages - 1), 4),
                     "font_size": fs, "updated_at": round(time.time(), 3)}
            if book.kind == "reflow":
                state["chapter"], state["offset"] = self._location(book, index)
        return state

    # ------------------------------------------------------------ cache
    def forget(self, book_id: str) -> None:
        """Fecha o livro e apaga o EPUB feito para ler (antes de tirá-lo da estante)."""
        with MUPDF_LOCK:
            book = self._books.pop(book_id, None)
            if book is not None:
                book.close()
        try:
            (self.cache / f"{book_id}.epub").unlink(missing_ok=True)
        except OSError:  # aberto por outro programa (Windows): fica para a próxima limpeza
            pass

    def close(self) -> None:
        with MUPDF_LOCK:
            for book in self._books.values():
                book.close()
            self._books.clear()

    def source(self, entry) -> Path:
        """O arquivo que o MuPDF abre: o próprio livro ou, no caso do Kindle, o EPUB feito dele.

        A conversão do Kindle acontece uma vez só, fora da trava do MuPDF; a prévia de
        páginas usa o mesmo EPUB."""
        path = Path(entry.path)
        target = self.cache / f"{entry.id}.epub"
        if entry.format == "mobi" and target.exists():
            return target
        if not path.exists():
            raise PreloError("O arquivo deste livro não foi encontrado.",
                             hint="Ele foi movido ou apagado? Adicione-o de novo à estante.")
        if entry.format != "mobi":
            return path
        with self._preparing:
            if not target.exists():
                from .pipeline import ConversionRequest, convert

                self.cache.mkdir(parents=True, exist_ok=True)
                tmp = Path(tempfile.mkdtemp(prefix="convertendo-", dir=self.cache))
                try:
                    result = convert(ConversionRequest(path, "epub", output_dir=tmp))
                    replace_file(result.output_path, target)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
        return target

    # ------------------------------------------------------------ internos
    def _open(self, entry, source: Path) -> OpenBook:
        book = self._books.get(entry.id)
        if book is not None:
            self._books.move_to_end(entry.id)
            return book
        book = self._load(entry.format, source)
        self._books[entry.id] = book
        while len(self._books) > self._size:
            _, old = self._books.popitem(last=False)
            old.close()
        return book

    def _load(self, fmt: str, source: Path) -> OpenBook:
        if fmt == "pdf":
            import pymupdf

            doc = pymupdf.open(source, filetype="pdf")
            if doc.needs_pass:
                doc.close()
                raise PreloError("Este PDF tem senha.",
                                 hint="Abra num leitor de PDF com a senha e salve uma cópia sem ela.")
            ratios = [doc[i].rect.width / max(1.0, doc[i].rect.height) for i in range(doc.page_count)]
            return OpenBook("fixed", doc=doc, ratios=ratios)
        if fmt in ("epub", "mobi"):  # o Kindle já chega aqui como o EPUB feito dele
            import pymupdf

            fixed, rtl, size = epub_shape(source)
            doc = pymupdf.open(source, filetype="epub")
            if doc.page_count == 0:
                doc.close()
                raise PreloError("O livro não tem páginas para mostrar.")
            if not fixed:
                return OpenBook("reflow", doc=doc, rtl=rtl)
            w, h = size or FIXED_EPUB_SIZE
            doc.layout(width=w, height=h, fontsize=16)  # cada arquivo do EPUB vira uma página do tamanho dele
            return OpenBook("fixed", doc=doc, ratios=[w / h] * doc.page_count, rtl=rtl)
        if fmt == "comic":
            with zipfile.ZipFile(source) as zf:
                names = comic_pages(zf.namelist())
                if not names:
                    raise PreloError("O quadrinho não tem imagens.")
                ratios = []
                for name in names:
                    with zf.open(name) as fh, Image.open(fh) as im:
                        ratios.append(im.width / max(1, im.height))
                info = zf.read("ComicInfo.xml").lower() if "ComicInfo.xml" in zf.namelist() else b""
            return OpenBook("fixed", zip_path=source, images=names, ratios=ratios,
                            rtl=b"yesandrighttoleft" in info)
        with Image.open(source) as im:
            return OpenBook("fixed", image_path=source, ratios=[im.width / max(1, im.height)])

    def _paginate(self, book: OpenBook, w: int, h: int, fs: int) -> None:
        if book.kind != "reflow" or book.layout == (w, h, fs):
            return
        book.doc.layout(width=w, height=h, fontsize=fs)
        book.layout = (w, h, fs)
        book.letters.clear()

    @staticmethod
    def _clamp(book: OpenBook, index: int) -> int:
        return max(0, min(book.pages - 1, index))

    def _toc(self, book: OpenBook) -> list[dict]:
        if book.doc is None:
            return []
        out = []
        for level, title, page in book.doc.get_toc(simple=True):
            title = " ".join(str(title).split())
            if title and page >= 1:
                out.append({"level": int(level), "title": title, "page": int(page)})
        return out

    def _image(self, book: OpenBook, index: int, w: int, h: int, dpr: float) -> Image.Image:
        if book.doc is not None:
            import pymupdf

            page = book.doc[index]
            rect = page.rect
            if book.kind == "reflow":  # a página já tem w × h: só a densidade da tela
                zoom = dpr
            else:
                zoom = min(w / max(1.0, rect.width), h / max(1.0, rect.height)) * dpr
            zoom = min(zoom, MAX_SIDE / max(1.0, rect.width, rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if book.zip_path is not None:
            with zipfile.ZipFile(book.zip_path) as zf, Image.open(io.BytesIO(zf.read(book.images[index]))) as im:
                im.load()
                img = flatten(im)
        else:
            with Image.open(book.image_path) as im:
                im.load()
                img = flatten(im)
        box = (min(MAX_SIDE, max(1, round(w * dpr))), min(MAX_SIDE, max(1, round(h * dpr))))
        if img.width > box[0] or img.height > box[1]:
            img.thumbnail(box, Image.Resampling.LANCZOS)  # só reduz: ampliar fica com a tela
        return img

    # ------------------------------------------------- onde parou (fluido)
    def _letters(self, book: OpenBook, index: int) -> int:
        """Letras da página, sem espaços (a quebra de linha muda com a paginação; as letras, não)."""
        if index not in book.letters:
            book.letters[index] = len("".join(book.doc[index].get_text("text").split()))
        return book.letters[index]

    def _location(self, book: OpenBook, index: int) -> tuple[int, int]:
        chapter, _ = book.doc.location_from_page_number(index)
        first = book.doc.page_number_from_location((chapter, 0))
        return int(chapter), sum(self._letters(book, p) for p in range(first, index))

    def _resume(self, book: OpenBook, reading: dict) -> int:
        """A página onde o leitor parou, na paginação atual (contada de 0)."""
        if not reading:
            return 0
        if book.kind == "fixed" and reading.get("page"):
            return self._clamp(book, int(reading["page"]) - 1)
        chapter, offset = reading.get("chapter"), reading.get("offset")
        if book.kind == "reflow" and chapter is not None and 0 <= int(chapter) < book.doc.chapter_count:
            first = book.doc.page_number_from_location((int(chapter), 0))
            if not offset:
                return first
            count = book.doc.chapter_page_count(int(chapter))
            seen = 0
            for i in range(count):
                seen += self._letters(book, first + i)
                if seen > int(offset):
                    return first + i
            return first + max(0, count - 1)
        return self._clamp(book, round(float(reading.get("fraction") or 0) * (book.pages - 1)))
