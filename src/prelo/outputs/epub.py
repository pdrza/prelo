"""Escritor EPUB 3 (com NCX para leitores EPUB 2).

* Layout fixo: ``rendition:layout pre-paginated`` + metadados equivalentes da
  Amazon (``fixed-layout``, ``original-resolution``), para o "Enviar para
  Kindle" também manter as páginas, e o arquivo de opções da Apple.
* Texto fluido: capa em SVG (como o Calibre, escala sem distorcer), lista de
  páginas do original e marcos de navegação.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .. import APP_NAME, __version__
from ..core.model import Book, Document, Resource
from .xhtml import build_xhtml, esc

MEDIA_FIX = {"image/jpg": "image/jpeg", "image/svg": "image/svg+xml", "text/xml": "application/xhtml+xml"}
STORED = {"image/jpeg", "image/png", "image/gif", "image/webp", "font/woff", "font/woff2", "application/font-woff"}


def _id(prefix: str, n: int) -> str:
    return f"{prefix}{n:04d}"


def _href(h: str) -> str:
    return quote(h, safe="/#-_.~")


def cover_document(book: Book) -> Document | None:
    """Página de capa em SVG para livros fluidos (o layout fixo já começa na capa)."""
    if book.layout == "fixed" or book.cover is None:
        return None
    from PIL import Image

    try:
        with Image.open(book.cover.path) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        w, h = 1200, 1800
    img = posixpath.relpath(book.cover.href, "text")
    body = (f'<div class="cover"><svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="100%" height="100%" '
            f'viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">'
            f'<image width="{w}" height="{h}" xlink:href="{esc(img)}"/></svg></div>')
    css = "body.cover{margin:0;padding:0;text-align:center}div.cover{margin:0;padding:0;height:100%}"
    return Document(href="text/cover.xhtml", title="Capa", body=body, id="cover",
                    properties={"svg"}, body_attrs={"class": "cover"}, inline_css=css)


def _toc_items(entries, depth=0) -> tuple[str, int]:
    out, max_depth = [], depth + 1
    for e in entries:
        child_html = ""
        if e.children:
            child_html, d = _toc_items(e.children, depth + 1)
            max_depth = max(max_depth, d)
        out.append(f'<li><a href="{esc(_href(e.href))}">{esc(e.title)}</a>{child_html}</li>')
    return "<ol>" + "".join(out) + "</ol>", max_depth


def nav_document(book: Book, cover_href: str | None) -> str:
    lang = esc(book.metadata.language or "pt")
    toc_html, _ = _toc_items(book.toc)
    parts = [f'<nav epub:type="toc" id="toc" role="doc-toc"><h1>Sumário</h1>{toc_html}</nav>']
    if book.page_list:
        items = "".join(f'<li><a href="{esc(_href(p.href))}">{esc(p.label)}</a></li>' for p in book.page_list)
        parts.append(f'<nav epub:type="page-list" id="page-list" hidden=""><h2>Páginas</h2><ol>{items}</ol></nav>')
    marks = []
    if cover_href:
        marks.append(f'<li><a epub:type="cover" href="{esc(_href(cover_href))}">Capa</a></li>')
    start = next((d.href for d in book.documents if d.linear), None)
    if start:
        marks.append(f'<li><a epub:type="bodymatter" href="{esc(_href(start))}">Início</a></li>')
    if marks:
        parts.append(f'<nav epub:type="landmarks" id="landmarks" hidden=""><h2>Marcos</h2><ol>{"".join(marks)}</ol></nav>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="{lang}" xml:lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n<title>Sumário</title>\n'
            '<style>ol{list-style:none;padding-left:1em}</style>\n</head>\n<body>\n'
            + "\n".join(parts) + "\n</body>\n</html>\n")


def ncx_document(book: Book) -> str:
    counter = [0]

    def points(entries) -> str:
        out = []
        for e in entries:
            counter[0] += 1
            n = counter[0]
            out.append(f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{esc(e.title)}</text></navLabel>'
                       f'<content src="{esc(_href(e.href))}"/>{points(e.children)}</navPoint>')
        return "".join(out)

    _, depth = _toc_items(book.toc)
    nav = points(book.toc)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{esc(book.metadata.language)}">\n'
            f'<head><meta name="dtb:uid" content="{esc(book.metadata.identifier)}"/>'
            f'<meta name="dtb:depth" content="{depth}"/><meta name="dtb:totalPageCount" content="0"/>'
            '<meta name="dtb:maxPageNumber" content="0"/></head>\n'
            f'<docTitle><text>{esc(book.metadata.title)}</text></docTitle>\n<navMap>{nav}</navMap>\n</ncx>\n')


def _metadata_xml(book: Book, cover_id: str | None) -> str:
    md = book.metadata
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = [f'<dc:identifier id="bookid">{esc(md.identifier)}</dc:identifier>',
         f"<dc:title>{esc(md.title or 'Sem título')}</dc:title>",
         f"<dc:language>{esc(md.language or 'pt')}</dc:language>"]
    for i, a in enumerate(md.authors, 1):
        m.append(f'<dc:creator id="creator{i}">{esc(a)}</dc:creator>')
        m.append(f'<meta refines="#creator{i}" property="role" scheme="marc:relators">aut</meta>')
    if md.publisher:
        m.append(f"<dc:publisher>{esc(md.publisher)}</dc:publisher>")
    if md.description:
        m.append(f"<dc:description>{esc(md.description)}</dc:description>")
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", md.date or ""):
        m.append(f"<dc:date>{md.date}</dc:date>")
    for s in md.subjects:
        m.append(f"<dc:subject>{esc(s)}</dc:subject>")
    if md.isbn:
        m.append(f'<dc:identifier id="isbn">urn:isbn:{esc(md.isbn)}</dc:identifier>')
    if md.rights:
        m.append(f"<dc:rights>{esc(md.rights)}</dc:rights>")
    m.append(f'<meta property="dcterms:modified">{now}</meta>')
    if md.series:
        m.append(f'<meta property="belongs-to-collection" id="series">{esc(md.series)}</meta>')
        m.append('<meta refines="#series" property="collection-type">series</meta>')
        if md.series_index is not None:
            m.append(f'<meta refines="#series" property="group-position">{md.series_index:g}</meta>')
        m.append(f'<meta name="calibre:series" content="{esc(md.series)}"/>')
        if md.series_index is not None:
            m.append(f'<meta name="calibre:series_index" content="{md.series_index:g}"/>')
    if cover_id:
        m.append(f'<meta name="cover" content="{cover_id}"/>')
    m.append(f'<meta name="generator" content="{APP_NAME} {__version__}"/>')
    if book.layout == "fixed":
        m.append('<meta property="rendition:layout">pre-paginated</meta>')
        m.append('<meta property="rendition:spread">none</meta>')
        m.append('<meta property="rendition:orientation">auto</meta>')
        m.append('<meta name="fixed-layout" content="true"/>')
        if book.viewport:
            m.append(f'<meta name="original-resolution" content="{book.viewport[0]}x{book.viewport[1]}"/>')
        if book.book_type:
            m.append(f'<meta name="book-type" content="{esc(book.book_type)}"/>')
        if book.direction == "rtl":
            m.append('<meta name="primary-writing-mode" content="horizontal-rl"/>')
    return "\n".join(m)


def write_epub(book: Book, dest: Path) -> None:
    cover_doc = cover_document(book)
    docs = ([cover_doc] if cover_doc else []) + book.documents
    doc_ids: dict[str, str] = {}
    res_ids: dict[str, str] = {}
    for n, d in enumerate(docs, 1):
        doc_ids[d.href] = "cover" if d is cover_doc else _id("d", n)
    for n, r in enumerate(book.resources, 1):
        res_ids[r.href] = "cover-image" if r is book.cover else _id("r", n)
    cover_id = res_ids.get(book.cover.href) if book.cover else None

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    for d in docs:
        props = set(d.properties) & {"svg", "scripted", "mathml", "remote-resources"}
        if "<svg" in d.body:
            props.add("svg")
        if "<math" in d.body:
            props.add("mathml")
        p = f' properties="{" ".join(sorted(props))}"' if props else ""
        manifest.append(f'<item id="{doc_ids[d.href]}" href="{esc(_href(d.href))}" '
                        f'media-type="application/xhtml+xml"{p}/>')
    for r in book.resources:
        mt = MEDIA_FIX.get(r.media_type, r.media_type)
        p = ' properties="cover-image"' if r is book.cover else ""
        manifest.append(f'<item id="{res_ids[r.href]}" href="{esc(_href(r.href))}" media-type="{esc(mt)}"{p}/>')

    spine = []
    for d in docs:
        attrs = "" if d.linear else ' linear="no"'
        props = set(d.properties) & {"page-spread-left", "page-spread-right"}
        if props:
            attrs += f' properties="{" ".join(sorted(props))}"'
        spine.append(f'<itemref idref="{doc_ids[d.href]}"{attrs}/>')
    ppd = f' page-progression-direction="{book.direction}"' if book.direction == "rtl" else ""
    guide = ""
    if cover_doc:
        guide = f'<guide><reference type="cover" title="Capa" href="{cover_doc.href}"/></guide>'

    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" '
           f'xml:lang="{esc(book.metadata.language)}">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           + _metadata_xml(book, cover_id) + "\n</metadata>\n<manifest>\n" + "\n".join(manifest)
           + f'\n</manifest>\n<spine toc="ncx"{ppd}>\n' + "\n".join(spine) + f"\n</spine>\n{guide}\n</package>\n")

    container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles>\n</container>\n")

    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        if book.layout == "fixed":
            zf.writestr("META-INF/com.apple.ibooks.display-options.xml",
                        '<?xml version="1.0" encoding="UTF-8"?>\n<display_options><platform name="*">'
                        '<option name="fixed-layout">true</option><option name="open-to-spread">false</option>'
                        "</platform></display_options>\n", compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav_document(book, cover_doc.href if cover_doc else
                                                     (book.documents[0].href if book.layout == "fixed" and book.documents else None)),
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx_document(book), compress_type=zipfile.ZIP_DEFLATED)
        for d in docs:
            zf.writestr(f"OEBPS/{d.href}", build_xhtml(d, book), compress_type=zipfile.ZIP_DEFLATED)
        for r in book.resources:
            mt = MEDIA_FIX.get(r.media_type, r.media_type)
            zf.write(r.path, f"OEBPS/{r.href}",
                     compress_type=zipfile.ZIP_STORED if mt in STORED else zipfile.ZIP_DEFLATED)


def resources_by_type(book: Book, prefix: str) -> list[Resource]:
    return [r for r in book.resources if r.media_type.startswith(prefix)]
