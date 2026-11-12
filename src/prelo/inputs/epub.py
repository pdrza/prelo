"""Leitor de EPUB (2 e 3, fluido ou de layout fixo)."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree
from PIL import Image

from ..core.errors import ConversionError
from ..core.imaging import Adjustments, apply_adjustments
from ..core.model import Book, Document, Metadata, PageTarget, Resource, TocEntry, resolve_href
from ..core.options import Level, Recommendation
from ..core.safety import check_epub_drm, safe_extract
from .base import InputContext, InputPlugin, populate_fixed_book
from .render import RenderConfig, render_all

NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "x": "http://www.w3.org/1999/xhtml",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "epub": "http://www.idpf.org/2007/ops",
    "svg": "http://www.w3.org/2000/svg",
    "xlink": "http://www.w3.org/1999/xlink",
}
XHTML = NS["x"]
DOC_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}

# Sem resolver entidades externas nem acessar a rede (proteção contra XXE).
XML_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)


def parse_xml(data: bytes) -> etree._Element:
    root = etree.fromstring(data, XML_PARSER)
    if root is None:
        raise ConversionError("Um arquivo XML do EPUB está ilegível.")
    return root


def parse_xhtml(data: bytes) -> etree._Element:
    from ..outputs.xhtml import numeric_entities

    # &nbsp; e afins não existem em XML: viram referências numéricas antes da análise
    data = numeric_entities(data.decode("utf-8", "replace")).encode("utf-8")
    root = None
    try:
        root = etree.fromstring(data, XML_PARSER)
    except etree.XMLSyntaxError:
        root = None
    if root is None or etree.QName(root).localname != "html":
        from lxml import html as lhtml

        root = lhtml.document_fromstring(data)
    return root


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def strip_xhtml_ns(root: etree._Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{%s}" % XHTML):
            el.tag = el.tag.split("}", 1)[1]
    etree.cleanup_namespaces(root)


def inner_xml(el: etree._Element) -> str:
    parts = [_esc(el.text or "")]
    for child in el:
        parts.append(etree.tostring(child, encoding="unicode", with_tail=True))
    return "".join(parts)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_fixed_layout(opf: etree._Element) -> bool:
    """Layout fixo declarado para o livro todo (``rendition:layout`` do EPUB 3 ou o
    ``fixed-layout`` da Amazon) ou na maioria das páginas, uma a uma. Uma página
    pré-paginada solta, como a capa de muitos livros fluidos, não torna o livro fixo."""
    if opf.xpath("//*[local-name()='meta'][@property='rendition:layout'][normalize-space()='pre-paginated']"):
        return True
    if opf.xpath("//*[local-name()='meta'][@name='fixed-layout'][normalize-space(@content)='true']"):
        return True
    refs = opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']")
    marked = sum("rendition:layout-pre-paginated" in (r.get("properties") or "").split() for r in refs)
    return marked * 2 > len(refs) > 0


def peek_layout(path: Path) -> str:
    """Lê só o OPF para saber se o EPUB é de layout fixo."""
    try:
        with zipfile.ZipFile(path) as zf:
            container = parse_xml(zf.read("META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", NS).get("full-path")
            opf = parse_xml(zf.read(rootfile))
    except Exception:  # noqa: BLE001
        return "reflow"
    return "fixed" if is_fixed_layout(opf) else "reflow"


class EpubInput(InputPlugin):
    format = "epub"

    def recommendations(self) -> list[Recommendation]:
        # MED: o usuário pode pedir layout fiel (o MuPDF pagina o EPUB) e a saída CBZ exige.
        return [Recommendation("mode", peek_layout(self.path), Level.MED)]

    def read(self, ctx: InputContext) -> Book:
        root_dir = ctx.ws.sub("epub")
        safe_extract(ctx.source, root_dir)
        check_epub_drm(root_dir)
        container = parse_xml((root_dir / "META-INF" / "container.xml").read_bytes())
        rf = container.find(".//c:rootfile", NS)
        if rf is None:
            raise ConversionError("EPUB inválido: container.xml sem rootfile.")
        opf_rel = rf.get("full-path")
        opf_path = root_dir / opf_rel
        opf_dir = opf_path.parent
        opf = parse_xml(opf_path.read_bytes())

        book = Book(source_format="epub")
        book.metadata = self._metadata(opf)
        manifest = {}
        for item in opf.iterfind(".//opf:manifest/opf:item", NS):
            href = unquote(item.get("href", ""))
            manifest[item.get("id")] = {
                "href": posixpath.normpath(href), "media": item.get("media-type", ""),
                "props": set((item.get("properties") or "").split()),
            }
        spine_el = opf.find(".//opf:spine", NS)
        if spine_el is None:
            raise ConversionError("EPUB inválido: sem spine (ordem de leitura).")
        book.direction = "rtl" if spine_el.get("page-progression-direction") == "rtl" else "ltr"
        fixed = is_fixed_layout(opf)

        if ctx.opts.mode == "fixed" and not fixed:
            return self._as_pages(ctx, book, opf_rel)

        spine_ids = []
        for ref in spine_el.iterfind("opf:itemref", NS):
            idref = ref.get("idref")
            if idref in manifest and manifest[idref]["media"] in DOC_TYPES:
                spine_ids.append((idref, ref.get("linear", "yes") != "no", set((ref.get("properties") or "").split())))

        nav_id = next((i for i, m in manifest.items() if "nav" in m["props"]), None)
        ncx_id = spine_el.get("toc") or next((i for i, m in manifest.items()
                                               if m["media"] == "application/x-dtbncx+xml"), None)
        spine_set = {s[0] for s in spine_ids}
        for mid, m in manifest.items():
            if mid in spine_set or mid in (nav_id, ncx_id) or m["media"] in DOC_TYPES:
                continue
            path = opf_dir / m["href"]
            if not path.exists():
                book.warn(f"Arquivo citado no EPUB não existe: {m['href']}")
                continue
            res = Resource(m["href"], m["media"], path, id=mid, properties=m["props"] & {"cover-image"})
            book.add_resource(res)

        for idx, (idref, linear, props) in enumerate(spine_ids):
            m = manifest[idref]
            path = opf_dir / m["href"]
            if not path.exists():
                book.warn(f"Capítulo citado no EPUB não existe: {m['href']}")
                continue
            book.documents.append(self._document(path.read_bytes(), m["href"], idref, linear,
                                                 props & {"page-spread-left", "page-spread-right"}, book))
            if idx % 10 == 0:
                ctx.progress(0.1 + 0.6 * idx / max(1, len(spine_ids)), "Lendo capítulos")

        book.layout = "fixed" if fixed else "reflow"
        if fixed:
            sizes = [d.viewport for d in book.documents if d.viewport]
            book.viewport = max(set(sizes), key=sizes.count) if sizes else None
        book.cover = self._cover(opf, manifest, book)
        book.toc, book.page_list = self._toc(opf_dir, manifest, nav_id, ncx_id)
        if book.layout == "fixed" or ctx.opts.apply_to_images:
            self._adjust_images(ctx, book)
        return book

    # ------------------------------------------------------------- partes
    def _metadata(self, opf) -> Metadata:
        md = Metadata()
        def first(xp):
            vals = [v.strip() for v in opf.xpath(xp, namespaces=NS) if v and v.strip()]
            return vals[0] if vals else ""
        md.title = first(".//opf:metadata/dc:title/text()") or "Sem título"
        md.authors = [a.strip() for a in opf.xpath(".//opf:metadata/dc:creator/text()", namespaces=NS) if a.strip()]
        md.language = first(".//opf:metadata/dc:language/text()") or "pt"
        md.publisher = first(".//opf:metadata/dc:publisher/text()")
        md.description = re.sub(r"<[^>]+>", "", first(".//opf:metadata/dc:description/text()"))
        md.date = first(".//opf:metadata/dc:date/text()")[:10]
        md.subjects = [s.strip() for s in opf.xpath(".//opf:metadata/dc:subject/text()", namespaces=NS) if s.strip()]
        md.rights = first(".//opf:metadata/dc:rights/text()")
        for ident in opf.xpath(".//opf:metadata/dc:identifier", namespaces=NS):
            val = (ident.text or "").strip()
            scheme = (ident.get("{%s}scheme" % NS["opf"]) or "").lower()
            if scheme == "isbn" or re.fullmatch(r"(urn:isbn:)?[\d-]{10,17}X?", val, re.I):
                md.isbn = re.sub(r"^urn:isbn:", "", val, flags=re.I)
        uid = opf.get("unique-identifier")
        if uid:
            val = first(f".//opf:metadata/dc:identifier[@id='{uid}']/text()")
            if val:
                md.identifier = val
        series = first(".//opf:metadata/opf:meta[@name='calibre:series']/@content") or \
            first(".//opf:metadata/opf:meta[@property='belongs-to-collection']/text()")
        md.series = series
        idx = first(".//opf:metadata/opf:meta[@name='calibre:series_index']/@content") or \
            first(".//opf:metadata/opf:meta[@property='group-position']/text()")
        try:
            md.series_index = float(idx) if idx else None
        except ValueError:
            md.series_index = None
        return md

    def _document(self, data: bytes, href: str, idref: str, linear: bool, props: set, book: Book) -> Document:
        root = parse_xhtml(data)
        strip_xhtml_ns(root)
        return document_from_root(root, href, idref, linear, props)

    def _cover(self, opf, manifest, book: Book) -> Resource | None:
        for res in book.resources:
            if "cover-image" in res.properties:
                return res
        cid = opf.xpath(".//opf:metadata/opf:meta[@name='cover']/@content", namespaces=NS)
        if cid and cid[0] in manifest:
            res = book.resource(manifest[cid[0]]["href"])
            if res is not None and res.is_image:
                res.properties.add("cover-image")
                return res
        for ref in opf.xpath(".//opf:guide/opf:reference[@type='cover']/@href", namespaces=NS):
            target = unquote(ref.split("#")[0])
            res = book.resource(target)
            if res is not None and res.is_image:
                return res
            doc = book.document(target)
            if doc is not None:
                m = re.search(r'(?:src|xlink:href|href)="([^"]+\.(?:jpe?g|png|gif|webp))"', doc.body, re.I)
                if m:
                    res = book.resource(resolve_href(doc.href, m.group(1)))
                    if res is not None:
                        return res
        if book.documents:
            m = re.search(r'(?:src|href)="([^"]+\.(?:jpe?g|png|gif))"', book.documents[0].body, re.I)
            if m and len(re.sub(r"<[^>]+>", "", book.documents[0].body).strip()) < 200:
                return book.resource(resolve_href(book.documents[0].href, m.group(1)))
        return None

    def _toc(self, opf_dir: Path, manifest, nav_id, ncx_id) -> tuple[list[TocEntry], list[PageTarget]]:
        toc: list[TocEntry] = []
        pages: list[PageTarget] = []
        if nav_id:
            nav_href = manifest[nav_id]["href"]
            nav_root = parse_xhtml((opf_dir / nav_href).read_bytes())
            strip_xhtml_ns(nav_root)
            for nav in nav_root.iter("nav"):
                kind = nav.get("{%s}type" % NS["epub"]) or nav.get("type") or ""
                ol = nav.find("ol")
                if ol is None:
                    continue
                if "toc" in kind and not toc:
                    toc = self._nav_list(ol, nav_href)
                elif "page-list" in kind:
                    for a in ol.iter("a"):
                        if a.get("href"):
                            pages.append(PageTarget("".join(a.itertext()).strip(),
                                                    resolve_href(nav_href, unquote(a.get("href")))))
        if not toc and ncx_id and ncx_id in manifest:
            ncx_href = manifest[ncx_id]["href"]
            ncx = parse_xml((opf_dir / ncx_href).read_bytes())
            nav_map = ncx.find("ncx:navMap", NS)
            if nav_map is not None:
                toc = self._ncx_points(nav_map, ncx_href)
        return toc, pages

    def _nav_list(self, ol, base: str) -> list[TocEntry]:
        out = []
        for li in ol.findall("li"):
            a = li.find("a")
            if a is None:
                a = li.find("span")
            title = " ".join("".join(a.itertext()).split()) if a is not None else ""
            href = resolve_href(base, unquote(a.get("href"))) if a is not None and a.get("href") else ""
            sub = li.find("ol")
            children = self._nav_list(sub, base) if sub is not None else []
            if href:
                out.append(TocEntry(title or "Sem título", href, children))
            else:
                out.extend(children)
        return out

    def _ncx_points(self, parent, base: str) -> list[TocEntry]:
        out = []
        for np_ in parent.findall("ncx:navPoint", NS):
            label = " ".join("".join(np_.find("ncx:navLabel", NS).itertext()).split()) \
                if np_.find("ncx:navLabel", NS) is not None else ""
            content = np_.find("ncx:content", NS)
            src = content.get("src") if content is not None else ""
            children = self._ncx_points(np_, base)
            if src:
                out.append(TocEntry(label or "Sem título", resolve_href(base, unquote(src)), children))
            else:
                out.extend(children)
        return out

    def _adjust_images(self, ctx: InputContext, book: Book) -> None:
        adjust_book_images(ctx, book)

    def _as_pages(self, ctx: InputContext, book: Book, opf_rel: str) -> Book:
        """Layout fiel para EPUB fluido: o MuPDF pagina o livro na proporção da tela."""
        import pymupdf

        W, H = ctx.profile.size or (1240, 1754)
        width_pt = 432.0
        height_pt = width_pt * H / W
        doc = pymupdf.open(ctx.source, filetype="epub")
        doc.layout(width=width_pt, height=height_pt, fontsize=11)
        count = doc.page_count
        toc_raw = doc.get_toc(simple=True)
        doc.close()
        img_dir = ctx.ws.sub("pages")
        cfg = RenderConfig(
            source=str(ctx.source), out_dir=str(img_dir), target=ctx.profile.size, dpi=ctx.opts.dpi,
            grayscale=ctx.grayscale, adjustments=Adjustments.from_options(ctx.opts).to_dict(),
            quality=ctx.opts.quality, fill_screen=bool(ctx.opts.fill_screen and ctx.profile.size),
            split_spreads=False, rtl=book.direction == "rtl", filetype="epub",
            reflow_layout=(width_pt, height_pt, 11.0),
        )
        pages = render_all(cfg, list(range(count)), workers=ctx.opts.workers,
                           progress=lambda f: ctx.progress(0.05 + 0.9 * f, "Paginando o EPUB"),
                           cancelled=ctx.cancelled)
        first = populate_fixed_book(book, pages, img_dir, ctx.ws)
        from .pdf import nest_toc

        entries = [(lvl, title, first[p - 1]) for lvl, title, p, *_ in toc_raw if (p - 1) in first]
        book.toc = nest_toc(entries or [(1, "Início", book.documents[0].href)])
        book.cover = book.resource(f"images/{pages[0].filename}")
        if book.cover is not None:
            book.cover.properties.add("cover-image")
        book.notes.append("O EPUB foi paginado para o layout fiel (cada página vira uma imagem).")
        return book


def document_from_root(root, href: str, idref: str = "", linear: bool = True, props: set | None = None) -> Document:
    """Documento do livro a partir do XHTML já analisado (sem namespace)."""
    head = root.find("head")
    body = root.find("body")
    title, stylesheets, inline_css, viewport = "", [], [], None
    if head is not None:
        t = head.find("title")
        title = (t.text or "").strip() if t is not None else ""
        for link in head.iter("link"):
            if "stylesheet" in (link.get("rel") or "").lower() and link.get("href"):
                stylesheets.append(resolve_href(href, unquote(link.get("href"))))
        for st in head.iter("style"):
            if st.text:
                inline_css.append(st.text)
        for meta in head.iter("meta"):
            if (meta.get("name") or "").lower() == "viewport":
                w = re.search(r"width\s*=\s*(\d+)", meta.get("content", ""))
                h = re.search(r"height\s*=\s*(\d+)", meta.get("content", ""))
                if w and h:
                    viewport = (int(w.group(1)), int(h.group(1)))
    if body is None:
        body_html, attrs = "", {}
    else:
        attrs = {k: v for k, v in body.attrib.items() if not k.startswith("{")}
        body_html = inner_xml(body)
    lang = root.get("lang") or root.get("{http://www.w3.org/XML/1998/namespace}lang")
    return Document(href=href, title=title, body=body_html, id=idref, linear=linear, properties=props or set(),
                    viewport=viewport, body_attrs=attrs, stylesheets=stylesheets,
                    inline_css="\n".join(inline_css), lang=lang)


def adjust_book_images(ctx: InputContext, book: Book) -> None:
    """Aplica os ajustes de página (temperatura, brilho...) às imagens do livro."""
    adj = Adjustments.from_options(ctx.opts, grayscale=ctx.grayscale)
    if adj.is_identity:
        return
    for res in book.resources:
        if res.media_type not in ("image/jpeg", "image/png"):
            continue
        with Image.open(res.path) as src:
            src.load()
            img = apply_adjustments(src, adj)
        buf = io.BytesIO()
        if res.media_type == "image/jpeg":
            img.save(buf, "JPEG", quality=90, optimize=True)
        else:
            img.save(buf, "PNG", optimize=True)
        res.path.write_bytes(buf.getvalue())

