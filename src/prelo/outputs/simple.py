"""Escritores CBZ, TXT e PDF."""

from __future__ import annotations

import html
import re
import tempfile
import zipfile
from pathlib import Path

import pymupdf

from ..core.errors import IncompatibleConversionError
from ..core.model import Book, Document, resolve_href
from .epub import write_epub
from .xhtml import esc

_IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"', re.I)


def page_image(book: Book, doc: Document):
    m = _IMG_SRC.search(doc.body)
    if not m:
        return None
    return book.resource(resolve_href(doc.href, html.unescape(m.group(1))))


# --------------------------------------------------------------------- CBZ
def write_cbz(book: Book, dest: Path) -> None:
    if book.layout != "fixed":
        raise IncompatibleConversionError("CBZ precisa de páginas em layout fiel.")
    md = book.metadata
    count = 0
    with zipfile.ZipFile(dest, "w") as zf:
        for doc in book.documents:
            res = page_image(book, doc)
            if res is None:
                continue
            count += 1
            ext = res.href.rsplit(".", 1)[-1]
            zf.write(res.path, f"{count:04d}.{ext}", compress_type=zipfile.ZIP_STORED)
        manga = "YesAndRightToLeft" if book.direction == "rtl" else "No"
        info = ['<?xml version="1.0" encoding="utf-8"?>',
                '<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
                f"<Title>{esc(md.title)}</Title>"]
        if md.series:
            info.append(f"<Series>{esc(md.series)}</Series>")
            if md.series_index is not None:
                info.append(f"<Number>{md.series_index:g}</Number>")
        if md.authors:
            info.append(f"<Writer>{esc(', '.join(md.authors))}</Writer>")
        if md.publisher:
            info.append(f"<Publisher>{esc(md.publisher)}</Publisher>")
        if md.description:
            info.append(f"<Summary>{esc(md.description)}</Summary>")
        info += [f"<LanguageISO>{esc(md.language)}</LanguageISO>", f"<PageCount>{count}</PageCount>",
                 f"<Manga>{manga}</Manga>", "</ComicInfo>"]
        zf.writestr("ComicInfo.xml", "\n".join(info), compress_type=zipfile.ZIP_DEFLATED)


# --------------------------------------------------------------------- TXT
_BLOCK_END = re.compile(r"</(p|div|h[1-6]|li|tr|blockquote|table|section|article)>|<br\s*/?>", re.I)


def html_to_text(markup: str) -> str:
    markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", markup, flags=re.S | re.I)
    markup = _BLOCK_END.sub("\n\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    paras = [re.sub(r"[ \t\r\f\v]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paras if p)


def write_txt(book: Book, dest: Path) -> None:
    if book.layout == "fixed":
        raise IncompatibleConversionError("TXT precisa de texto fluido (o livro só tem páginas em imagem).")
    md = book.metadata
    head = md.title + ("\n" + ", ".join(md.authors) if md.authors else "")
    body = "\n\n".join(html_to_text(d.body) for d in book.documents if d.linear)
    dest.write_text(f"{head}\n\n{body}\n", encoding="utf-8")


# --------------------------------------------------------------------- PDF
def _toc_list(book: Book, page_of: dict[str, int]) -> list[list]:
    out = []
    for depth, entry in book.iter_toc():
        page = page_of.get(entry.href.split("#")[0])
        if page:
            out.append([depth + 1, entry.title, page])
    # set_toc exige que o primeiro nível seja 1 e que não haja saltos de nível
    fixed, last = [], 0
    for lvl, title, page in out:
        lvl = min(lvl, last + 1)
        fixed.append([lvl, title, page])
        last = lvl
    return fixed


def _set_meta(pdf: pymupdf.Document, book: Book) -> None:
    md = book.metadata
    pdf.set_metadata({"title": md.title, "author": ", ".join(md.authors), "subject": md.description[:500],
                      "keywords": ", ".join(md.subjects), "creator": "Prelo", "producer": "Prelo"})


def write_pdf(book: Book, dest: Path, *, source_pdf: Path | None = None, text_layer: bool = True,
              page_size: tuple[int, int] | None = None, log=print) -> None:
    if book.layout == "reflow":
        _write_pdf_reflow(book, dest, page_size)
        return
    out = pymupdf.open()
    src = pymupdf.open(source_pdf) if (source_pdf and text_layer) else None
    page_of: dict[str, int] = {}
    skipped_text = 0
    try:
        for doc in book.documents:
            res = page_image(book, doc)
            if res is None or not doc.viewport:
                continue
            w_px, h_px = doc.viewport
            if doc.source_size:
                w_pt = doc.source_size[0]
                h_pt = w_pt * h_px / w_px
            else:
                w_pt, h_pt = 595.0, 595.0 * h_px / w_px
            page = out.new_page(width=w_pt, height=h_pt)
            page_of[doc.href] = page.number + 1
            if src is not None and doc.source_page is not None and doc.source_size:
                sw, sh = doc.source_size
                if abs(w_px / h_px - sw / sh) < 0.01:
                    # texto original (vetorial) por baixo: busca e seleção continuam funcionando
                    page.show_pdf_page(page.rect, src, doc.source_page)
                else:
                    skipped_text += 1
            page.insert_image(page.rect, filename=str(res.path), keep_proportion=False, overlay=True)
        if skipped_text:
            log(f"{skipped_text} página(s) recortadas ou divididas ficaram sem camada de texto.")
        _set_meta(out, book)
        toc = _toc_list(book, page_of)
        if toc:
            out.set_toc(toc)
        out.save(dest, garbage=3, deflate=True)
    finally:
        out.close()
        if src is not None:
            src.close()


def _write_pdf_reflow(book: Book, dest: Path, page_size: tuple[int, int] | None) -> None:
    """Pagina o texto fluido com o motor de layout do MuPDF."""
    w, h = page_size or (1240, 1754)
    width_pt = 420.0
    height_pt = width_pt * h / w
    with tempfile.TemporaryDirectory(prefix="prelo-pdf-") as tmp:
        epub_path = Path(tmp) / "book.epub"
        write_epub(book, epub_path)
        doc = pymupdf.open(epub_path, filetype="epub")
        doc.layout(width=width_pt, height=height_pt, fontsize=11)
        toc = doc.get_toc()
        pdf = pymupdf.open("pdf", doc.convert_to_pdf())
        doc.close()
        _set_meta(pdf, book)
        if toc:
            try:
                pdf.set_toc(toc)
            except Exception:  # noqa: BLE001 - sumário malformado não impede o PDF
                pass
        pdf.save(dest, garbage=3, deflate=True)
        pdf.close()
