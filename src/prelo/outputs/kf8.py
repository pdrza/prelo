"""Escritor AZW3 (KF8), sem Calibre nem KindleGen.

Como o KF8 guarda o livro:

* Todo o XHTML vira um texto só ("flow 0"). Cada arquivo é dividido em um
  **esqueleto** (``<html><head>…</head><body></body></html>``) seguido dos
  **fragmentos** com o conteúdo do ``<body>``. Tabelas de índice dizem onde
  cada fragmento entra no esqueleto.
* Elementos recebem atributos ``aid`` (base 32). Links internos apontam para
  ``kindle:pos:fid:<fragmento>:off:<deslocamento>``.
* CSS fica em flows separados (``kindle:flow:0001``), imagens e fontes em
  registros de recurso (``kindle:embed:0001``).
* O sumário vira um índice NCX; metadados vão no EXTH, inclusive os de
  layout fixo (``fixed-layout``, ``original-resolution``).

Regras do Calibre reaproveitadas: nada de ``aid`` dentro de tabelas (o
renderizador do Kindle trava em tabelas grandes com ``aid``), entradas do NCX
ordenadas por (profundidade, posição), guia ordenado por tipo.
"""

from __future__ import annotations

import bisect
import io
import re
import struct
import uuid as uuidlib
import zlib
from dataclasses import dataclass, field

from lxml import etree
from PIL import Image

from ..core.errors import ConversionError
from ..core.imaging import to_jpeg_bytes
from ..core.model import Book, resolve_href
from .palmdoc import (CNCX, END_TAG, EOF_RECORD, FLIS, NULL, RECORD_SIZE, TagMeta, build_index, exth_block,
                      fcis, mobi_language, palmdb, text_records, to_base32)
from .tbs import calculate_tbs
from .xhtml import close_non_void, numeric_entities

CHUNK_SIZE = 8192
XHTML_NS = "http://www.w3.org/1999/xhtml"
AID_TAGS = {
    "a", "abbr", "address", "article", "aside", "audio", "b", "bdo", "blockquote", "body", "button",
    "cite", "code", "dd", "del", "details", "dfn", "div", "dl", "dt", "em", "fieldset", "figcaption",
    "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "i", "ins", "kbd",
    "label", "legend", "li", "map", "mark", "meter", "nav", "ol", "output", "p", "pre", "progress",
    "q", "rp", "rt", "samp", "section", "select", "small", "span", "strong", "sub", "summary", "sup",
    "textarea", "time", "ul", "var", "video",
}
DROP_TAGS = {"script", "style", "link", "meta", "title", "head", "noscript", "iframe", "object", "embed"}
FONT_TYPES = {"font/ttf", "font/otf", "font/sfnt", "application/x-font-ttf", "application/x-font-truetype",
              "application/vnd.ms-opentype", "application/font-sfnt", "application/x-font-opentype"}

SKEL_TAGS = (TagMeta("chunk_count", 1, 1, 3, 0), TagMeta("geometry", 6, 2, 12, 0), END_TAG)
CHUNK_TAGS = (TagMeta("cncx_offset", 2, 1, 1, 0), TagMeta("file_number", 3, 1, 2, 0),
              TagMeta("sequence_number", 4, 1, 4, 0), TagMeta("geometry", 6, 2, 8, 0), END_TAG)
GUIDE_TAGS = (TagMeta("title", 1, 1, 1, 0), TagMeta("pos_fid", 6, 2, 2, 0), END_TAG)
NCX_TAGS = (TagMeta("offset", 1, 1, 1, 0), TagMeta("length", 2, 1, 2, 0), TagMeta("label", 3, 1, 4, 0),
            TagMeta("depth", 4, 1, 8, 0), TagMeta("parent", 21, 1, 16, 0),
            TagMeta("first_child", 22, 1, 32, 0), TagMeta("last_child", 23, 1, 64, 0),
            TagMeta("pos_fid", 6, 2, 128, 0), END_TAG)


# ------------------------------------------------------------- recursos
class Resources:
    """Imagens e fontes em registros. Índices ``kindle:embed`` começam em 1."""

    def __init__(self, book: Book, log):
        self.records: list[bytes] = []
        self.index: dict[str, int] = {}
        self.mime: dict[str, str] = {}
        self.cover_offset: int | None = None
        self.thumb_offset: int | None = None
        self.has_fonts = False
        self.log = log
        ordered = sorted(book.resources, key=lambda r: r is not book.cover)
        for res in ordered:
            if res.is_image:
                self._add_image(res.href, res.read_bytes(), res.media_type)
            elif res.media_type in FONT_TYPES or res.href.lower().endswith((".ttf", ".otf")):
                self._add_font(res.href, res.read_bytes())
            elif res.is_font:
                log(f"Fonte {res.href} ignorada: o Kindle só aceita TTF/OTF.")
        if book.cover is not None and book.cover.href in self.index:
            self.cover_offset = self.index[book.cover.href] - 1
            thumb = io.BytesIO()
            with Image.open(book.cover.path) as im:
                im = im.convert("RGB")
                im.thumbnail((330, 470), Image.Resampling.LANCZOS)
                im.save(thumb, "JPEG", quality=85)
            self.records.append(thumb.getvalue())
            self.thumb_offset = len(self.records) - 1

    def _add_image(self, href: str, data: bytes, media_type: str) -> None:
        mime = media_type
        try:
            if media_type == "image/svg+xml":
                import pymupdf

                doc = pymupdf.open(stream=data, filetype="svg")
                pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                data, mime = pix.tobytes("png"), "image/png"
            else:
                with Image.open(io.BytesIO(data)) as im:
                    fmt, mode = im.format, im.mode
                if fmt not in ("JPEG", "PNG", "GIF") or mode == "CMYK" or len(data) > 5_000_000:
                    data, mime = to_jpeg_bytes(data, 88, max_side=3200), "image/jpeg"
                else:
                    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif"}[fmt]
        except Exception as exc:  # noqa: BLE001
            self.log(f"Imagem {href} ignorada: {exc}")
            return
        self.records.append(data)
        self.index[href] = len(self.records)
        self.mime[href] = mime

    def _add_font(self, href: str, data: bytes) -> None:
        compressed = zlib.compress(data, 9)
        header = b"FONT" + struct.pack(">5L", len(data), 0b1, 24, 0, 24)
        self.records.append(header + compressed)
        self.index[href] = len(self.records)
        self.mime[href] = "application/x-font-ttf" if href.lower().endswith(".ttf") else "application/vnd.ms-opentype"
        self.has_fonts = True

    def embed(self, href: str) -> str | None:
        idx = self.index.get(href)
        if idx is None:
            return None
        return f"kindle:embed:{to_base32(idx, 4)}?mime={self.mime[href]}"


# ----------------------------------------------------------------- CSS
_CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)


def css_for_kf8(css: str, base_href: str, res: Resources) -> str:
    def repl(m: re.Match) -> str:
        url = m.group(2).strip()
        if url.startswith(("data:", "http:", "https:", "kindle:")):
            return m.group(0)
        embed = res.embed(resolve_href(base_href, url).split("#")[0])
        return f"url({embed})" if embed else m.group(0)
    css = re.sub(r"@namespace[^;]*;", "", css)
    css = re.sub(r"@import[^;]*;", "", css)
    return _CSS_URL.sub(repl, css)


# ------------------------------------------------------------ documentos
@dataclass
class KF8File:
    head: bytes
    tail: bytes
    chunks: list[bytes]
    body_aid: str
    ids: dict[str, str] = field(default_factory=dict)


def _parse_body(body: str) -> etree._Element:
    markup = numeric_entities(body)
    wrapper = (f'<body xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops" '
               f'xmlns:xlink="http://www.w3.org/1999/xlink">{markup}</body>')
    try:
        return etree.fromstring(wrapper.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True,
                                                                          huge_tree=True))
    except etree.XMLSyntaxError:
        from lxml import html as lhtml

        return lhtml.fragment_fromstring(body, create_parent="body")


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


class DocumentBuilder:
    def __init__(self, book: Book, res: Resources, log):
        self.book, self.res, self.log = book, res, log
        self.links: dict[bytes, tuple[str, str]] = {}
        self.cid = 0

    def placeholder(self, target_doc: str, frag: str) -> str:
        n = len(self.links)
        ph = f"kindle:pos:fid:0000:off:{to_base32(n, 10)}"
        self.links[ph.encode("ascii")] = (target_doc, frag)
        return ph

    def build(self, file_no: int, doc, css_links: list[str]) -> KF8File:
        root = _parse_body(doc.body)
        # tira namespaces (o KF8 usa HTML 5 sem namespaces, como o Calibre faz)
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            el.tag = _local(el.tag)
            for k in list(el.attrib):
                if "}" in k:
                    v = el.attrib.pop(k)
                    ns, name = k[1:].split("}", 1)
                    if ns == "http://www.w3.org/XML/1998/namespace" and name == "lang":
                        el.set("lang", v)
                    elif ns == "http://www.w3.org/1999/xlink" and name == "href":
                        el.set("href", v)
        etree.cleanup_namespaces(root)
        for el in list(root.iter()):
            if not isinstance(el.tag, str):
                _drop(el)
            elif el.tag in DROP_TAGS:
                _drop(el)
        for svg in list(root.iter("svg")):
            self._svg(svg, doc.href)

        aidbase = file_no * 1_000_000
        j = 1
        ids: dict[str, str] = {}
        body_aid = to_base32(aidbase)
        for el in root.iter():
            if el is root or not isinstance(el.tag, str):
                continue
            tag = el.tag.lower()
            if tag == "img":
                self._img(el, doc.href)
            elif tag == "a" and el.get("href"):
                self._link(el, doc.href)
            for k in ("role",):
                el.attrib.pop(k, None)
            el_id = el.get("id")
            if el_id is not None or tag in AID_TAGS:
                if any(a.tag == "table" for a in el.iterancestors()) or tag == "table":
                    if el_id:
                        self.cid += 1
                        el.set("cid", f"c{self.cid}")
                        ids[el_id] = f"c{self.cid}"
                else:
                    aid = to_base32(aidbase + j)
                    j += 1
                    el.set("aid", aid)
                    if el_id:
                        ids[el_id] = aid

        lang = doc.lang or self.book.metadata.language or "pt"
        head = ["<head>", f"<title>{_esc(doc.title or self.book.metadata.title)}</title>",
                '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>']
        if doc.viewport:
            head.append(f'<meta name="viewport" content="width={doc.viewport[0]}, height={doc.viewport[1]}"/>')
        for link in css_links:
            head.append(f'<link href="{link}" rel="stylesheet" type="text/css"/>')
        head.append("</head>")
        attrs = dict(doc.body_attrs)
        if doc.viewport and "style" not in attrs:
            attrs["style"] = f"width:{doc.viewport[0]}px;height:{doc.viewport[1]}px;margin:0;padding:0"
        body_attrs = "".join(f' {k}="{_esc(v)}"' for k, v in attrs.items()
                             if re.fullmatch(r"[A-Za-z_][\w.-]*", k))
        head_html = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     f'<html xmlns="{XHTML_NS}" lang="{_esc(lang)}">' + "".join(head)
                     + f'<body{body_attrs} aid="{body_aid}">')
        pieces = [_esc(root.text or "")] if (root.text or "").strip() else []
        for child in root:
            pieces.append(close_non_void(etree.tostring(child, encoding="unicode", method="xml", with_tail=True)))
        chunks: list[bytes] = []
        cur = b""
        for piece in pieces:
            raw = piece.encode("utf-8")
            if cur and len(cur) + len(raw) > CHUNK_SIZE:
                chunks.append(cur)
                cur = raw
            else:
                cur += raw
        if cur.strip() or not chunks:
            chunks.append(cur if cur.strip() else b"<div></div>")
        return KF8File(head_html.encode("utf-8"), b"</body></html>", chunks, body_aid, ids)

    def _img(self, el, doc_href: str) -> None:
        src = el.get("src") or el.get("href") or ""
        embed = self.res.embed(resolve_href(doc_href, src).split("#")[0]) if src else None
        if embed is None:
            self.log(f"Imagem não encontrada: {src}")
            _drop(el)
            return
        el.attrib.pop("href", None)
        el.set("src", embed)

    def _link(self, el, doc_href: str) -> None:
        href = el.get("href")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
            return  # externo (http, mailto...)
        target = resolve_href(doc_href, href)
        path, _, frag = target.partition("#")
        if self.book.document(path) is None:
            el.attrib.pop("href", None)
            return
        el.set("href", self.placeholder(path, frag))

    def _svg(self, svg, doc_href: str) -> None:
        """SVG embutido: a capa padrão (svg > image) vira <img>; o resto é rasterizado."""
        images = [e for e in svg.iter() if isinstance(e.tag, str) and e.tag == "image"]
        if len(images) == 1 and images[0].get("href"):
            img = etree.Element("img", src=images[0].get("href"), alt="")
            img.tail = svg.tail
            svg.getparent().replace(svg, img)
            return
        try:
            import pymupdf

            svg.set("xmlns", "http://www.w3.org/2000/svg")
            raw = etree.tostring(svg, encoding="utf-8")
            doc = pymupdf.open(stream=raw, filetype="svg")
            png = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
            href = f"_svg/{len(self.res.records) + 1}.png"
            self.res.records.append(png)
            self.res.index[href] = len(self.res.records)
            self.res.mime[href] = "image/png"
            img = etree.Element("img", src=self.res.embed(href) or "", alt="")
            img.tail = svg.tail
            svg.getparent().replace(svg, img)
        except Exception:  # noqa: BLE001
            _drop(svg)


def _drop(el) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail or ""
    prev = el.getprevious()
    if tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ------------------------------------------------------------- montagem
class KF8Writer:
    def __init__(self, book: Book, opts, log=print):
        self.book, self.opts, self.log = book, opts, log
        if not book.documents:
            raise ConversionError("O livro não tem conteúdo para gravar.")
        self.res = Resources(book, log)
        self.flows: list[bytes] = [b""]
        self.css_flow: dict[str, int] = {}

    def _css_link(self, key: str, css: str, base: str) -> str:
        if key not in self.css_flow:
            self.flows.append(css_for_kf8(css, base, self.res).encode("utf-8"))
            self.css_flow[key] = len(self.flows) - 1
        return f"kindle:flow:{to_base32(self.css_flow[key], 4)}?mime=text/css"

    def build(self) -> bytes:
        book = self.book
        builder = DocumentBuilder(book, self.res, self.log)
        files: list[KF8File] = []
        for n, doc in enumerate(book.documents):
            links = []
            for href in doc.stylesheets + [s for s in book.stylesheets if s not in doc.stylesheets]:
                res = book.resource(href)
                if res is not None:
                    links.append(self._css_link(href, res.path.read_text("utf-8", "replace"), href))
            if doc.inline_css.strip():
                links.append(self._css_link("inline:" + str(hash(doc.inline_css)), doc.inline_css, doc.href))
            files.append(builder.build(n, doc, links))

        # ---- texto (flow 0), tabelas de esqueleto e fragmentos
        text = bytearray()
        rebuilt = bytearray()
        skel_table, chunk_table = [], []
        seq = 0
        cncx_selectors = []
        for fno, f in enumerate(files):
            start = len(text)
            text += f.head + f.tail
            insert = start + len(f.head)
            cp = 0
            selector = f"P-//*[@aid='{f.body_aid}']"
            cncx_selectors.append(selector)
            for chunk in f.chunks:
                chunk_table.append((insert, selector, fno, seq, cp, len(chunk)))
                text += chunk
                insert += len(chunk)
                cp += len(chunk)
                seq += 1
            skel_table.append((fno, len(f.chunks), start, len(f.head) + len(f.tail)))
            rebuilt += f.head + b"".join(f.chunks) + f.tail

        aid_pos: dict[str, int] = {}
        for m in re.finditer(rb'<[^>]+? [ac]id="([cA-Z0-9]+)"', bytes(rebuilt)):
            aid_pos.setdefault(m.group(1).decode("ascii"), m.start())
        starts = [c[0] for c in chunk_table]

        def pos_fid(offset: int) -> tuple[int, int]:
            k = bisect.bisect_right(starts, offset) - 1
            if k < 0:
                return chunk_table[0][3], 0
            ins, _sel, _f, sq, _cp, length = chunk_table[k]
            if offset < ins + length:
                return sq, offset - ins
            if k + 1 < len(chunk_table):  # está no esqueleto: usa o próximo fragmento
                return chunk_table[k + 1][3], 0
            return sq, offset - ins

        doc_index = {d.href: n for n, d in enumerate(book.documents)}

        def target_offset(href: str) -> int:
            path, _, frag = href.partition("#")
            n = doc_index.get(path, 0)
            f = files[n]
            aid = f.ids.get(frag) if frag else None
            return aid_pos.get(aid or f.body_aid, aid_pos.get(f.body_aid, 0))

        raw = bytes(text)
        for ph, (path, frag) in builder.links.items():
            off = target_offset(f"{path}#{frag}" if frag else path)
            sq, o = pos_fid(off)
            final = f"kindle:pos:fid:{to_base32(sq, 4)}:off:{to_base32(o, 10)}".encode("ascii")
            raw = raw.replace(ph, final)
        self.flows[0] = raw

        # ---- índices
        chunk_cncx = CNCX(cncx_selectors)
        chunk_entries = [(f"{c[0]:010d}".encode(), {"cncx_offset": chunk_cncx[c[1]], "file_number": c[2],
                                                     "sequence_number": c[3], "geometry": (c[4], c[5])})
                         for c in chunk_table]
        skel_entries = [(f"SKEL{s[0]:010d}".encode(), {"chunk_count": (s[1], s[1]),
                                                        "geometry": (s[2], s[3], s[2], s[3])})
                        for s in skel_table]
        ncx_records, ncx_table = self._ncx(target_offset, pos_fid, len(raw))
        start_doc = next((d for d in book.documents if d.linear and d.href != "text/cover.xhtml"),
                         book.documents[0])
        start_off = target_offset(start_doc.href)
        guide_cncx = CNCX(["Início"])
        guide_records = build_index([(b"text", {"title": guide_cncx["Início"], "pos_fid": pos_fid(start_off)})],
                                    GUIDE_TAGS, guide_cncx)

        # ---- registros
        all_text = b"".join(self.flows)
        records: list[bytes] = [b""]
        trecs = text_records(all_text)
        extra_flags = 0b1
        if ncx_table:  # TBS: o Kindle usa para pular de capítulo em capítulo
            lengths = [min(RECORD_SIZE, len(all_text) - i * RECORD_SIZE) for i in range(len(trecs))]
            trecs = [rec + tbs for rec, tbs in zip(trecs, calculate_tbs(ncx_table, lengths))]
            extra_flags = 0b11
        records += trecs
        last_text = len(trecs)
        size = sum(len(r) for r in trecs)
        if size % 4:
            records.append(b"\0" * (4 - size % 4))
        first_non_text = len(records)
        chunk_index = len(records)
        records += build_index(chunk_entries, CHUNK_TAGS, chunk_cncx)
        skel_index = len(records)
        records += build_index(skel_entries, SKEL_TAGS)
        guide_index = len(records)
        records += guide_records
        ncx_index = NULL
        if ncx_records:
            ncx_index = len(records)
            records += ncx_records
        first_resource = NULL
        if self.res.records:
            first_resource = len(records)
            records += self.res.records
        fdst_record = len(records)
        pos = 0
        sections = b""
        for fl in self.flows:
            sections += struct.pack(">II", pos, pos + len(fl))
            pos += len(fl)
        records.append(b"FDST" + struct.pack(">II", 12, len(self.flows)) + sections)
        flis_record = len(records)
        records.append(FLIS)
        fcis_record = len(records)
        records.append(fcis(len(all_text), kf8=True))
        records.append(EOF_RECORD)

        records[0] = self._record0(
            text_length=len(all_text), last_text=last_text, first_non_text=first_non_text,
            chunk_index=chunk_index, skel_index=skel_index, guide_index=guide_index, ncx_index=ncx_index,
            first_resource=first_resource, fdst_record=fdst_record, fdst_count=len(self.flows),
            flis_record=flis_record, fcis_record=fcis_record, start_offset=start_off, extra_flags=extra_flags)
        return palmdb(book.metadata.title, records)

    def _ncx(self, target_offset, pos_fid, text_len: int) -> tuple[list[bytes], list[dict]]:
        flat: list[dict] = []

        def walk(entries, depth: int, parent: int | None) -> None:
            for e in entries:
                me = len(flat)
                off = target_offset(e.href)
                flat.append({"id": me, "label": e.title or "Sem título", "depth": depth, "offset": off,
                             "pos_fid": pos_fid(off), "parent_id": parent, "children": []})
                if parent is not None:
                    flat[parent]["children"].append(me)
                walk(e.children, depth + 1, me)

        walk(self.book.toc, 0, None)
        if not flat:
            return [], []
        entries = sorted(flat, key=lambda e: (e["depth"], e["offset"]))
        new_index = {e["id"]: i for i, e in enumerate(entries)}
        cncx = CNCX([e["label"] for e in entries])
        width = max(2, len(f"{len(entries) - 1:X}"))
        out = []
        table = []
        for i, e in enumerate(entries):
            nxt = [x["offset"] for x in entries if x["depth"] <= e["depth"] and x["offset"] > e["offset"]]
            tags = {"offset": e["offset"], "length": max(1, (min(nxt) if nxt else text_len) - e["offset"]),
                    "label": cncx[e["label"]], "depth": e["depth"], "pos_fid": e["pos_fid"]}
            if e["parent_id"] is not None:
                tags["parent"] = new_index[e["parent_id"]]
            if e["children"]:
                kids = sorted(new_index[c] for c in e["children"])
                tags["first_child"], tags["last_child"] = kids[0], kids[-1]
            out.append((f"{i:0{width}X}".encode(), tags))
            table.append({"index": i, "offset": tags["offset"], "length": tags["length"], "depth": e["depth"],
                          "parent": tags.get("parent"), "first_child": tags.get("first_child"),
                          "last_child": tags.get("last_child"), "label": e["label"]})
        return build_index(out, NCX_TAGS, cncx), table

    def _exth(self, start_offset: int) -> bytes:
        md = self.book.metadata
        recs: list[tuple[int, bytes | int]] = []
        for a in md.authors:
            recs.append((100, a.encode("utf-8")))
        if md.publisher:
            recs.append((101, md.publisher.encode("utf-8")))
        if md.description:
            recs.append((103, md.description.encode("utf-8")))
        if md.isbn:
            recs.append((104, md.isbn.encode("utf-8")))
        for s in md.subjects:
            recs.append((105, s.encode("utf-8")))
        if md.date:
            recs.append((106, md.date.encode("utf-8")))
        if md.rights:
            recs.append((109, md.rights.encode("utf-8")))
        ident = md.identifier.split(":")[-1] if md.identifier.startswith("urn:uuid:") else str(uuidlib.uuid4())
        recs.append((113, ident.encode("ascii")))
        recs.append((504, ident.encode("ascii")))
        recs.append((501, (self.opts.kindle_doc_type if self.opts else "EBOK").encode("ascii")))
        recs.append((503, md.title.encode("utf-8")))
        recs.append((524, (md.language or "pt").encode("ascii", "ignore")))
        rtl = self.book.direction == "rtl"
        recs.append((525, b"horizontal-rl" if rtl else b"horizontal-lr"))
        if rtl:
            recs.append((527, b"rtl"))
        if self.res.cover_offset is not None:
            recs.append((201, self.res.cover_offset))
            recs.append((203, 0))
            recs.append((129, f"kindle:embed:{to_base32(self.res.cover_offset + 1, 4)}".encode("ascii")))
        if self.res.thumb_offset is not None:
            recs.append((202, self.res.thumb_offset))
        recs.append((125, len(self.res.records)))
        # identidade do kindlegen 2.9, como o Calibre faz, para o Kindle tratar igual
        recs += [(204, 201), (205, 2), (206, 9), (207, 0), (535, b"0730-890adc2")]
        recs.append((116, start_offset))
        if self.book.layout == "fixed":
            recs.append((122, b"true"))
            if self.book.viewport:
                recs.append((126, f"{self.book.viewport[0]}x{self.book.viewport[1]}".encode("ascii")))
            recs.append((127, b"true"))
            recs.append((128, b"true"))
            if self.book.book_type:
                recs.append((123, self.book.book_type.encode("ascii")))
        return exth_block(recs)

    def _record0(self, *, text_length, last_text, first_non_text, chunk_index, skel_index, guide_index,
                 ncx_index, first_resource, fdst_record, fdst_count, flis_record, fcis_record,
                 start_offset, extra_flags) -> bytes:
        title = self.book.metadata.title.encode("utf-8")
        exth = self._exth(start_offset)
        exth_flags = 0b1010000 | (0b1000000000000 if self.res.has_fonts else 0)
        r = bytearray()
        r += struct.pack(">HHIHHHH", 2, 0, text_length, last_text, 4096, 0, 0)
        r += b"MOBI" + struct.pack(">IIII", 264, 2, 65001, uuidlib.uuid4().int & 0xFFFFFFFF)
        r += struct.pack(">I", 8)                       # 36 versão do arquivo
        r += struct.pack(">II", chunk_index, NULL)      # 40 orth (= índice de fragmentos), 44 infl
        r += struct.pack(">8I", *([NULL] * 8))          # 48 índices extras
        r += struct.pack(">I", first_non_text)          # 80
        r += struct.pack(">II", 280 + len(exth), len(title))  # 84 título
        r += struct.pack(">I", mobi_language(self.book.metadata.language))  # 92
        r += struct.pack(">II", 0, 0)                   # 96 idiomas de dicionário
        r += struct.pack(">I", 8)                       # 104 versão mínima
        r += struct.pack(">I", first_resource)          # 108
        r += struct.pack(">IIII", 0, 0, 0, 0)           # 112 huffman
        r += struct.pack(">I", exth_flags)              # 128
        r += b"\0" * 32                                 # 132
        r += struct.pack(">I", NULL)                    # 164
        r += struct.pack(">IIII", NULL, 0, 0, 0)        # 168 DRM: nenhum
        r += b"\0" * 8                                  # 184
        r += struct.pack(">II", fdst_record, fdst_count)  # 192
        r += struct.pack(">II", fcis_record, 1)         # 200
        r += struct.pack(">II", flis_record, 1)         # 208
        r += b"\0" * 8                                  # 216
        r += struct.pack(">II", NULL, 0)                # 224 SRCS
        r += struct.pack(">II", NULL, NULL)             # 232
        r += struct.pack(">I", extra_flags)             # 240 bytes extras: multibyte (+ TBS)
        r += struct.pack(">5I", ncx_index, chunk_index, skel_index, NULL, guide_index)  # 244
        r += struct.pack(">4I", NULL, 0, NULL, 0)       # 264
        assert len(r) == 280, len(r)
        return bytes(r) + exth + title + b"\0" * 8192


def write_azw3(book: Book, dest, opts=None, *, log=print) -> None:
    data = KF8Writer(book, opts, log).build()
    with open(dest, "wb") as fh:
        fh.write(data)
