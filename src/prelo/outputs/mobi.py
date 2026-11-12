"""Escritor MOBI 6 (Kindle antigo).

Compatível com todos os Kindles, mas limitado: sem CSS, sem layout fixo de
verdade (cada página do layout fiel vira uma imagem ajustada à tela), imagens
só em JPEG/GIF (como o Calibre faz por padrão). Para Kindles atuais, AZW3 é
melhor.
"""

from __future__ import annotations

import bisect
import io
import re
import struct
import uuid as uuidlib

from lxml import etree
from PIL import Image

from ..core.errors import ConversionError
from ..core.imaging import to_jpeg_bytes
from ..core.model import Book, resolve_href
from .kf8 import DROP_TAGS, _drop, _esc, _local, _parse_body
from .palmdoc import EOF_RECORD, FLIS, NULL, exth_block, fcis, mobi_language, palmdb, text_records
from .xhtml import close_non_void

RENAME = {"em": "i", "strong": "b", "section": "div", "article": "div", "figure": "div", "nav": "div",
          "aside": "div", "header": "div", "footer": "div", "main": "div", "figcaption": "div"}
KEEP_ATTRS = {"id", "href", "align", "colspan", "rowspan", "recindex", "filepos"}
PLACEHOLDER = b"filepos=@@@@@@@@@@"


class MobiWriter:
    def __init__(self, book: Book, opts, log=print):
        if not book.documents:
            raise ConversionError("O livro não tem conteúdo para gravar.")
        self.book, self.opts, self.log = book, opts, log
        self.images: list[bytes] = []
        self.recindex: dict[str, int] = {}
        self.cover_offset: int | None = None
        self.thumb_offset: int | None = None
        self.links: list[tuple[str, str]] = []
        self._prepare_images()

    def _prepare_images(self) -> None:
        book = self.book
        for res in sorted((r for r in book.resources if r.is_image), key=lambda r: r is not book.cover):
            try:
                data = res.read_bytes()
                if res.media_type != "image/gif":
                    data = to_jpeg_bytes(data, 90)
            except Exception as exc:  # noqa: BLE001
                self.log(f"Imagem {res.href} ignorada: {exc}")
                continue
            self.images.append(data)
            self.recindex[res.href] = len(self.images)
        if book.cover is not None and book.cover.href in self.recindex:
            self.cover_offset = self.recindex[book.cover.href] - 1
            with Image.open(book.cover.path) as im:
                im = im.convert("RGB")
                im.thumbnail((330, 470), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
            self.images.append(buf.getvalue())
            self.thumb_offset = len(self.images) - 1

    def _body(self, doc) -> str:
        root = _parse_body(doc.body)
        for el in list(root.iter()):
            if not isinstance(el.tag, str) or _local(el.tag) in DROP_TAGS:
                _drop(el)
        for el in list(root.iter()):
            if not isinstance(el.tag, str):
                continue
            el.tag = _local(el.tag)
            tag = el.tag
            if tag == "svg":
                imgs = [e for e in el.iter() if isinstance(e.tag, str) and _local(e.tag) == "image"]
                href = next((v for k, v in imgs[0].attrib.items() if k.endswith("href")), None) if imgs else None
                if href:
                    img = etree.Element("img", src=href)
                    img.tail = el.tail
                    el.getparent().replace(el, img)
                    el, tag = img, "img"
                else:
                    _drop(el)
                    continue
            if tag in RENAME:
                el.tag = tag = RENAME[tag]
            cls = el.get("class", "")
            attrs = {k: v for k, v in el.attrib.items() if _local(k) in KEEP_ATTRS and "}" not in k}
            if tag == "img":
                src = el.get("src", "")
                idx = self.recindex.get(resolve_href(doc.href, src).split("#")[0])
                if idx is None:
                    _drop(el)
                    continue
                attrs = {"recindex": f"{idx:05d}"}
            if tag == "a" and "href" in attrs:
                href = attrs["href"]
                if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
                    target = resolve_href(doc.href, href)
                    path, _, frag = target.partition("#")
                    del attrs["href"]
                    if self.book.document(path) is not None:
                        self.links.append((path, frag))
                        attrs["filepos"] = "@@@@@@@@@@"
            if tag in ("p", "div") and re.search(r"\bc\b", cls):
                attrs["align"] = "center"
            el.attrib.clear()
            for k, v in attrs.items():
                el.set(k, v)
        etree.cleanup_namespaces(root)
        pieces = [_esc(root.text or "")]
        for child in root:
            pieces.append(etree.tostring(child, encoding="unicode", method="xml", with_tail=True))
        html = close_non_void("".join(pieces))
        # filepos não leva aspas no MOBI
        return re.sub(r'filepos="(@{10})"', r"filepos=\1", html)

    def _inline_toc(self) -> str:
        def items(entries) -> str:
            out = []
            for e in entries:
                path, _, frag = e.href.partition("#")
                self.links.append((path, frag))
                out.append(f'<p><a filepos=@@@@@@@@@@>{_esc(e.title)}</a></p>')
                if e.children:
                    out.append(f"<blockquote>{items(e.children)}</blockquote>")
            return "".join(out)
        return f'<div id="prelo-toc"><h2>Sumário</h2>{items(self.book.toc)}</div>'

    def build(self) -> bytes:
        book = self.book
        mode = getattr(self.opts, "inline_toc", "auto") if self.opts else "auto"
        want_toc = mode in ("auto", "yes") and book.toc_count() > 1
        head = b"<html><head><guide>"
        guide_toc = want_toc
        if guide_toc:
            head += b'<reference type="toc" title="Sum\xc3\xa1rio" filepos=@@@@@@@@@@ />'
            self.links.append(("__toc__", ""))
        head += b'<reference type="text" title="In\xc3\xadcio" filepos=@@@@@@@@@@ />'
        start_doc = next((d for d in book.documents if d.linear), book.documents[0])
        self.links.append((start_doc.href, ""))
        head += b"</guide></head><body>"
        parts = [head]
        pos = len(head)
        doc_start: dict[str, int] = {}
        for n, doc in enumerate(book.documents):
            doc_start[doc.href] = pos
            html = self._body(doc).encode("utf-8")
            if n < len(book.documents) - 1 or want_toc:
                html += b"<mbp:pagebreak/>"
            parts.append(html)
            pos += len(html)
        toc_pos = pos
        if want_toc:
            toc = self._inline_toc().encode("utf-8")
            parts.append(toc)
            pos += len(toc)
        parts.append(b"</body></html>")
        text = b"".join(parts)

        # posições de cada id, por documento
        starts = sorted((p, h) for h, p in doc_start.items())
        start_pos = [p for p, _ in starts]
        ids: dict[tuple[str, str], int] = {}
        for m in re.finditer(rb'<[a-zA-Z][^<>]*?\sid="([^"]+)"', text):
            k = bisect.bisect_right(start_pos, m.start()) - 1
            if k >= 0:
                ids.setdefault((starts[k][1], m.group(1).decode("utf-8", "replace")), m.start())

        def resolve(path: str, frag: str) -> int:
            if path == "__toc__":
                return toc_pos
            if frag and (path, frag) in ids:
                return ids[(path, frag)]
            return doc_start.get(path, doc_start[book.documents[0].href])

        out = bytearray()
        last = 0
        k = 0
        for m in re.finditer(rb"filepos=@{10}", text):
            out += text[last:m.start()]
            path, frag = self.links[k] if k < len(self.links) else (book.documents[0].href, "")
            out += b"filepos=%010d" % resolve(path, frag)
            last = m.end()
            k += 1
        out += text[last:]
        text = bytes(out)

        records: list[bytes] = [b""]
        trecs = text_records(text)
        records += trecs
        size = sum(len(r) for r in trecs)
        if size % 4:
            records.append(b"\0" * (4 - size % 4))
        first_non_text = len(records)
        first_image = len(records)
        records += self.images
        last_content = len(records) - 1
        flis_record = len(records)
        records.append(FLIS)
        fcis_record = len(records)
        records.append(fcis(len(text), kf8=False))
        records.append(EOF_RECORD)
        records[0] = self._record0(len(text), len(trecs), first_non_text,
                                   first_image if self.images else NULL, last_content, flis_record, fcis_record)
        return palmdb(book.metadata.title, records)

    def _record0(self, text_length, last_text, first_non_text, first_image, last_content,
                 flis_record, fcis_record) -> bytes:
        md = self.book.metadata
        title = md.title.encode("utf-8")
        recs: list[tuple[int, bytes | int]] = [(100, a.encode("utf-8")) for a in md.authors]
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
        ident = md.identifier.split(":")[-1] if md.identifier.startswith("urn:uuid:") else str(uuidlib.uuid4())
        recs.append((113, ident.encode("ascii")))
        recs.append((501, (self.opts.kindle_doc_type if self.opts else "EBOK").encode("ascii")))
        recs.append((503, title))
        recs.append((524, (md.language or "pt").encode("ascii", "ignore")))
        if self.cover_offset is not None:
            recs += [(201, self.cover_offset), (203, 0)]
        if self.thumb_offset is not None:
            recs.append((202, self.thumb_offset))
        recs += [(204, 201), (205, 1), (206, 2), (207, 33307)]  # identidade do kindlegen 1.2 (Calibre)
        exth = exth_block(recs)
        r = bytearray()
        r += struct.pack(">HHIHHHH", 2, 0, text_length, last_text, 4096, 0, 0)
        r += b"MOBI" + struct.pack(">IIII", 232, 2, 65001, uuidlib.uuid4().int & 0xFFFFFFFF)
        r += struct.pack(">I", 6)                       # 36 versão
        r += struct.pack(">II", NULL, NULL)             # 40, 44
        r += struct.pack(">8I", *([NULL] * 8))          # 48
        r += struct.pack(">I", first_non_text)          # 80
        r += struct.pack(">II", 248 + len(exth), len(title))  # 84
        r += struct.pack(">I", mobi_language(md.language))   # 92
        r += struct.pack(">II", 0, 0)                   # 96
        r += struct.pack(">I", 6)                       # 104 versão mínima
        r += struct.pack(">I", first_image)             # 108
        r += struct.pack(">IIII", 0, 0, 0, 0)           # 112
        r += struct.pack(">I", 0x50)                    # 128 EXTH presente
        r += b"\0" * 32                                 # 132
        r += struct.pack(">I", NULL)                    # 164
        r += struct.pack(">IIII", NULL, 0, 0, 0)        # 168 sem DRM
        r += b"\0" * 8                                  # 184
        r += struct.pack(">HHI", 1, last_content, 1)    # 192 primeiro/último registro de conteúdo
        r += struct.pack(">II", fcis_record, 1)         # 200
        r += struct.pack(">II", flis_record, 1)         # 208
        r += b"\0" * 8                                  # 216
        r += struct.pack(">II", NULL, 0)                # 224
        r += struct.pack(">II", NULL, NULL)             # 232
        r += struct.pack(">I", 1)                       # 240 bytes extras: multibyte
        r += struct.pack(">I", NULL)                    # 244 sem índice NCX
        assert len(r) == 248, len(r)
        return bytes(r) + exth + title + b"\0" * 8192


def write_mobi(book: Book, dest, opts=None, *, log=print) -> None:
    data = MobiWriter(book, opts, log).build()
    with open(dest, "wb") as fh:
        fh.write(data)
