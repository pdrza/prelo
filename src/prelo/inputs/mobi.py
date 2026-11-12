"""Leitor de livros Kindle: MOBI 6, KF8 (AZW3) e arquivos mistos (MOBI 6 + KF8).

Um arquivo Kindle é um banco PalmDB. O registro 0 traz os cabeçalhos (PalmDOC,
MOBI e EXTH, com os metadados); seguem os registros de texto comprimido e,
depois, imagens, fontes e índices.

- **MOBI 6**: o texto é um HTML antigo, com ``<mbp:pagebreak/>`` entre os
  capítulos, links ``filepos`` (posição em bytes no texto) e imagens ``recindex``.
- **KF8**: o texto guarda o "esqueleto" de cada arquivo XHTML e, à parte, os
  fragmentos que entram nele. CSS e SVG ficam em fluxos separados (tabela FDST)
  e os links usam ``kindle:pos:fid``, ``kindle:embed`` e ``kindle:flow``.
- **Mistos** trazem as duas versões; a KF8 tem preferência por ter CSS.

Pontos conferidos no Calibre 9.18 (leitores ``mobi6``/``mobi8``, analisados a
partir do bytecode): as duas faixas de recursos dos arquivos mistos, os
registros que não são recursos, as fontes com zlib/XOR e a correção de
fragmentos com posição de inserção errada.
"""

from __future__ import annotations

import bisect
import html
import io
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from lxml import html as lhtml
from PIL import Image

from ..core.errors import ConversionError, DrmError
from ..core.model import Book, Document, Metadata, Resource, TocEntry
from ..core.options import Level, Recommendation
from ..outputs.palmdoc import decompress_doc
from .base import InputContext, InputPlugin
from .epub import adjust_book_images, document_from_root, inner_xml, parse_xhtml, strip_xhtml_ns

NULL = 0xFFFFFFFF
# Registros que ficam na faixa de recursos mas não são imagem nem fonte.
NOT_RESOURCES = {b"FLIS", b"FCIS", b"FDST", b"DATP", b"SRCS", b"CMET", b"PAGE", b"RESC", b"BOUN",
                 b"AUDI", b"VIDE", b"CRES", b"CONT", b"kind", b"\xe9\x8e\r\n", b"\xa0\xa0\xa0\xa0"}
EXTH_AUTHOR, EXTH_PUBLISHER, EXTH_DESCRIPTION, EXTH_ISBN = 100, 101, 103, 104
EXTH_SUBJECT, EXTH_DATE, EXTH_RIGHTS, EXTH_KF8_BOUNDARY = 105, 106, 109, 121
EXTH_FIXED_LAYOUT, EXTH_COVER, EXTH_TITLE, EXTH_LANGUAGE = 122, 201, 503, 524
EXTH_RESOLUTION = 307


# ------------------------------------------------------------------ binário
def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">L", data, off)[0] if 0 <= off <= len(data) - 4 else NULL


def _vwi(data: bytes, pos: int) -> tuple[int, int]:
    """Inteiro de largura variável "para a frente" (o bit 8 marca o último byte)."""
    value = used = 0
    while pos + used < len(data):
        b = data[pos + used]
        used += 1
        value = (value << 7) | (b & 0x7F)
        if b & 0x80:
            break
    return value, used


def _trailing_size(rec: bytes, flags: int) -> int:
    """Bytes extras no fim de um registro de texto (índices de navegação e o
    pedaço de caractere multibyte repetido do registro seguinte)."""
    size = 0
    bits = flags >> 1
    while bits:
        if bits & 1:  # cada entrada termina com o próprio tamanho, escrito de trás para frente
            value = shift = 0
            pos = len(rec) - size - 1
            while pos >= 0 and shift < 28:
                b = rec[pos]
                value |= (b & 0x7F) << shift
                shift += 7
                pos -= 1
                if b & 0x80:
                    break
            size += value
        bits >>= 1
    if flags & 1 and len(rec) > size:
        size += (rec[len(rec) - size - 1] & 0x3) + 1
    return min(size, len(rec))


class HuffCdic:
    """Descompressão HUFF/CDIC (Mobipocket): códigos de Huffman que apontam
    para frases de um dicionário; uma frase pode estar comprimida também."""

    def __init__(self, records: list[bytes]):
        if not records or records[0][:8] != b"HUFF\x00\x00\x00\x18":
            raise ConversionError("O livro usa uma compressão HUFF/CDIC inválida.")
        huff = records[0]
        off_cache, off_limits = struct.unpack_from(">LL", huff, 8)
        self.cache = []
        for v in struct.unpack_from(">256L", huff, off_cache):
            length, terminal, maxcode = v & 0x1F, bool(v & 0x80), v >> 8
            self.cache.append((length, terminal, ((maxcode + 1) << (32 - length)) - 1 if length else 0))
        limits = struct.unpack_from(">64L", huff, off_limits)
        self.mincode = [0] + [limits[2 * i] << (31 - i) for i in range(32)]
        self.maxcode = [(1 << 32) - 1] + [((limits[2 * i + 1] + 1) << (31 - i)) - 1 for i in range(32)]
        self.phrases: list[list] = []
        for cdic in records[1:]:
            if cdic[:8] != b"CDIC\x00\x00\x00\x10":
                raise ConversionError("O dicionário HUFF/CDIC do livro está corrompido.")
            total, bits = struct.unpack_from(">LL", cdic, 8)
            n = min(1 << bits, total - len(self.phrases))
            for off in struct.unpack_from(f">{n}H", cdic, 16):
                blen = struct.unpack_from(">H", cdic, 16 + off)[0]
                self.phrases.append([cdic[18 + off:18 + off + (blen & 0x7FFF)], bool(blen & 0x8000)])

    def unpack(self, data: bytes, depth: int = 0) -> bytes:
        if depth > 64:
            raise ConversionError("O texto comprimido do livro está corrompido.")
        bits_left = len(data) * 8
        data += b"\0" * 8
        pos, n = 0, 32
        x = int.from_bytes(data[0:8], "big")
        out = []
        while True:
            if n <= 0:
                pos += 4
                x = int.from_bytes(data[pos:pos + 8], "big")
                n += 32
            code = (x >> n) & 0xFFFFFFFF
            length, terminal, maxcode = self.cache[code >> 24]
            if not terminal:
                while length < 32 and code < self.mincode[length]:
                    length += 1
                maxcode = self.maxcode[length]
            if not length:
                raise ConversionError("O texto comprimido do livro está corrompido.")
            n -= length
            bits_left -= length
            if bits_left < 0:
                break
            index = (maxcode - code) >> (32 - length)
            if index >= len(self.phrases):
                raise ConversionError("O texto comprimido do livro está corrompido.")
            phrase = self.phrases[index]
            if not phrase[1]:  # frase ainda comprimida: expande uma vez e guarda
                if phrase[1] is None:
                    raise ConversionError("O dicionário HUFF/CDIC do livro tem um ciclo.")
                phrase[1] = None
                phrase[0] = self.unpack(phrase[0], depth + 1)
                phrase[1] = True
            out.append(phrase[0])
        return b"".join(out)


# ------------------------------------------------------------------ cabeçalhos
@dataclass
class Header:
    """Registro 0 de uma seção (MOBI 6 ou KF8). Índices são relativos a ``section``."""

    section: int
    compression: int
    text_length: int
    text_records: int
    encryption: int
    is_mobi: bool = False
    codepage: int = 1252
    version: int = 0
    first_resource: int = NULL
    huff_offset: int = NULL
    huff_count: int = 0
    extra_flags: int = 0
    ncx_index: int = NULL
    fdst_index: int = NULL
    frag_index: int = NULL
    skel_index: int = NULL
    full_name: str = ""
    exth: dict[int, list[bytes]] = field(default_factory=dict)

    @classmethod
    def parse(cls, rec0: bytes, section: int) -> "Header":
        if len(rec0) < 16:
            raise ConversionError("O cabeçalho do livro Kindle está truncado.")
        comp, _, text_len, text_recs, _, enc = struct.unpack_from(">HHLHHH", rec0, 0)
        h = cls(section, comp, text_len, text_recs, enc)
        if rec0[16:20] != b"MOBI":
            return h  # PalmDOC simples (TEXtREAd)
        h.is_mobi = True
        hlen = _u32(rec0, 20)
        h.codepage = _u32(rec0, 28)
        h.version = _u32(rec0, 36)
        h.first_resource = _u32(rec0, 0x6C)
        h.huff_offset, h.huff_count = _u32(rec0, 0x70), _u32(rec0, 0x74)
        end = 16 + hlen
        if end >= 0xF4:
            h.extra_flags = struct.unpack_from(">H", rec0, 0xF2)[0]
        if end >= 0xF8:
            h.ncx_index = _u32(rec0, 0xF4)
        if h.version >= 8 and end >= 0x108:
            h.fdst_index = _u32(rec0, 0xC0)
            h.frag_index = _u32(rec0, 0xF8)
            h.skel_index = _u32(rec0, 0xFC)
        if _u32(rec0, 0x80) & 0x40 and rec0[end:end + 4] == b"EXTH":
            count, pos = _u32(rec0, end + 8), end + 12
            for _ in range(count):
                kind, size = _u32(rec0, pos), _u32(rec0, pos + 4)
                if size < 8 or pos + size > len(rec0):
                    break
                h.exth.setdefault(kind, []).append(rec0[pos + 8:pos + size])
                pos += size
        name_off, name_len = _u32(rec0, 0x54), _u32(rec0, 0x58)
        if name_off != NULL and name_len != NULL:
            h.full_name = rec0[name_off:name_off + name_len].decode(h.encoding, "replace")
        return h

    @property
    def encoding(self) -> str:
        return "utf-8" if self.codepage == 65001 else "cp1252"

    def texts(self, key: int) -> list[str]:
        return [v.decode(self.encoding, "replace").strip("\0 ") for v in self.exth.get(key, [])]

    def text(self, key: int) -> str:
        values = self.texts(key)
        return values[0] if values else ""

    def number(self, key: int) -> int | None:
        values = self.exth.get(key)
        return struct.unpack(">L", values[0])[0] if values and len(values[0]) == 4 else None

    def absolute(self, index: int) -> int | None:
        return None if index in (NULL, None) else self.section + index


class KindleFile:
    """O PalmDB aberto, com a seção que vai ser lida (KF8 quando houver)."""

    def __init__(self, data: bytes):
        if len(data) < 86 or data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
            raise ConversionError("Não é um livro Kindle (MOBI/AZW3) válido.")
        count = struct.unpack_from(">H", data, 76)[0]
        offsets = [_u32(data, 78 + 8 * i) for i in range(count)] + [len(data)]
        if any(b < a for a, b in zip(offsets, offsets[1:])) or offsets[0] > len(data):
            raise ConversionError("A tabela de registros do livro Kindle está corrompida.")
        self.records = [data[offsets[i]:offsets[i + 1]] for i in range(count)]
        self.mobi6 = Header.parse(self.records[0], 0)
        self.header = self.mobi6
        self.kf8: Header | None = None
        if self.mobi6.version >= 8:
            self.kf8 = self.mobi6
        else:
            boundary = self.mobi6.number(EXTH_KF8_BOUNDARY)
            for cand in (boundary, (boundary or 0) + 1) if boundary not in (None, NULL) else ():
                if 0 < cand < len(self.records) and self.records[cand][16:20] == b"MOBI":
                    h = Header.parse(self.records[cand], cand)
                    if h.version >= 8:
                        self.kf8 = h
                        break
        if self.kf8 is not None:
            self.header = self.kf8
        for h in (self.mobi6, self.kf8):
            if h is not None and h.encryption:
                raise DrmError("Este livro Kindle tem DRM e não pode ser convertido.",
                               hint="Use um arquivo sem proteção (por exemplo, o EPUB original).")

    @property
    def joint(self) -> bool:
        return self.kf8 is not None and self.kf8 is not self.mobi6

    def resource_slots(self) -> list[bytes | None]:
        """Registros de recursos na ordem de ``kindle:embed``/``recindex`` (a partir de 1)."""
        h = self.header
        start = h.absolute(h.first_resource)
        if start is None or start >= len(self.records):
            start = h.section + 1 + h.text_records
        slots: list[bytes | None] = list(self.records[start:])
        if self.joint and not any(s and _image_ext(s) for s in slots):
            # alguns arquivos mistos guardam as imagens só antes da fronteira
            first6 = self.mobi6.absolute(self.mobi6.first_resource) or (1 + self.mobi6.text_records)
            slots = list(self.records[first6:h.section])
        return slots

    def text(self, h: Header, progress=None) -> bytes:
        if h.compression == 17480:
            first = h.absolute(h.huff_offset)
            if first is None:
                raise ConversionError("O livro usa HUFF/CDIC mas não traz o dicionário.")
            unpack = HuffCdic(self.records[first:first + h.huff_count]).unpack
        elif h.compression == 2:
            unpack = decompress_doc
        elif h.compression == 1:
            unpack = bytes
        else:
            raise ConversionError(f"O livro usa uma compressão desconhecida ({h.compression}).")
        out = []
        for i in range(h.text_records):
            k = h.section + 1 + i
            if k >= len(self.records):
                break
            rec = self.records[k]
            cut = _trailing_size(rec, h.extra_flags) if h.extra_flags else 0
            out.append(unpack(rec[:len(rec) - cut]))
            if progress and i % 25 == 0:
                progress(i / max(1, h.text_records))
        text = b"".join(out)
        if h.version >= 8:
            # No KF8 quem manda é a tabela FDST: há geradores que declaram no cabeçalho só o
            # tamanho do fluxo 0, e o CSS vem depois dele.
            return text
        return text[:h.text_length] if 0 < h.text_length < len(text) else text


# ------------------------------------------------------------------ índices INDX
def _tag_values(entry: bytes, control_count: int, tagx: list[tuple[int, int, int, int]]) -> dict[int, list[int]]:
    controls, pos, ci, wanted = entry[:control_count], control_count, 0, []
    for tag, per_entry, mask, end_flag in tagx:
        if end_flag:
            ci += 1
            continue
        if ci >= len(controls):
            break
        value = controls[ci] & mask
        if not value:
            continue
        if value == mask and bin(mask).count("1") > 1:
            nbytes, used = _vwi(entry, pos)  # o que vem é o tamanho em bytes dos valores
            pos += used
            wanted.append((tag, None, nbytes, per_entry))
        else:
            m = mask
            while not m & 1:
                m >>= 1
                value >>= 1
            wanted.append((tag, value, None, per_entry))
    out: dict[int, list[int]] = {}
    for tag, count, nbytes, per_entry in wanted:
        values = []
        if count is not None:
            for _ in range(count * per_entry):
                v, used = _vwi(entry, pos)
                pos += used
                values.append(v)
        else:
            consumed = 0
            while consumed < nbytes and pos < len(entry):
                v, used = _vwi(entry, pos)
                pos += used
                consumed += used
                values.append(v)
        out[tag] = values
    return out


def read_index(records: list[bytes], start: int | None, encoding: str = "utf-8"
               ) -> tuple[list[tuple[bytes, dict[int, list[int]]]], dict[int, str]]:
    """Entradas (rótulo, tags) de um índice INDX e as strings do CNCX."""
    if start is None or not 0 <= start < len(records) or records[start][:4] != b"INDX":
        return [], {}
    head = records[start]
    hlen, nrecs, ncncx = _u32(head, 4), _u32(head, 0x18), _u32(head, 0x34)
    tpos = head.find(b"TAGX", min(hlen, len(head)))
    if tpos < 0 or nrecs == NULL:
        return [], {}
    tlen, control_count = struct.unpack_from(">LL", head, tpos + 4)
    tagx = [tuple(head[p:p + 4]) for p in range(tpos + 12, tpos + tlen - 3, 4)]
    cncx: dict[int, str] = {}
    for i in range(ncncx if ncncx != NULL else 0):
        k = start + nrecs + 1 + i
        if k >= len(records):
            break
        rec, pos = records[k], 0
        while pos < len(rec):
            length, used = _vwi(rec, pos)
            if length:
                cncx[i * 0x10000 + pos] = rec[pos + used:pos + used + length].decode(encoding, "replace")
            pos += used + length
    entries = []
    for r in range(1, nrecs + 1):
        k = start + r
        if k >= len(records) or records[k][:4] != b"INDX":
            continue
        rec = records[k]
        idxt, count = _u32(rec, 0x14), _u32(rec, 0x18)
        if rec[idxt:idxt + 4] != b"IDXT" or idxt + 4 + 2 * count > len(rec):
            continue
        offsets = [*struct.unpack_from(f">{count}H", rec, idxt + 4), idxt]
        for a, b in zip(offsets, offsets[1:]):
            entry = rec[a:b]
            if entry:
                n = entry[0]
                entries.append((entry[1:1 + n], _tag_values(entry[1 + n:], control_count, tagx)))
    return entries, cncx


# ------------------------------------------------------------------ recursos
def _image_ext(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    return None


def _font(rec: bytes) -> tuple[bytes, str] | None:
    """Registro FONT: fonte com zlib e/ou os primeiros bytes embaralhados (XOR)."""
    try:
        _, flags, start, key_len, key_start = struct.unpack_from(">LLLLL", rec, 4)
        data = bytearray(rec[start:])
        if flags & 0b10 and key_len:
            key = rec[key_start:key_start + key_len]
            for i in range(min(1040, len(data))):
                data[i] ^= key[i % key_len]
        if flags & 0b1:
            data = bytearray(zlib.decompress(bytes(data)))
    except (struct.error, zlib.error):
        return None
    return bytes(data), "otf" if data[:4] == b"OTTO" else "ttf"


class Resources:
    """Grava imagens e fontes no espaço de trabalho; ``href(n)`` dá o caminho no livro."""

    def __init__(self, slots: list[bytes | None], book: Book, folder: Path):
        self.hrefs: dict[int, str] = {}
        for n, rec in enumerate(slots, start=1):
            if not rec or len(rec) < 4 or rec[:4] in NOT_RESOURCES:
                continue
            if rec[:4] == b"FONT":
                font = _font(rec)
                if font is None:
                    book.warn(f"Uma fonte embutida (registro {n}) não pôde ser lida e foi ignorada.")
                    continue
                data, ext = font
                href, media = f"fonts/font{n:05d}.{ext}", f"font/{ext}"
            else:
                ext = _image_ext(rec)
                if ext is None:
                    continue
                data, media = rec, {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}.get(ext)
                if ext == "bmp":  # o EPUB não aceita BMP
                    with Image.open(io.BytesIO(rec)) as im:
                        buf = io.BytesIO()
                        im.convert("RGB").save(buf, "PNG")
                    data, ext, media = buf.getvalue(), "png", "image/png"
                href = f"images/img{n:05d}.{ext}"
            path = folder / href.replace("/", "_")
            path.write_bytes(data)
            book.add_resource(Resource(href, media, path))
            self.hrefs[n] = href

    def href(self, n: int) -> str | None:
        return self.hrefs.get(n)


# ------------------------------------------------------------------ metadados
def _author(name: str) -> str:
    """"Hemingway, Ernest" (forma de catálogo comum nos arquivos Kindle) → "Ernest Hemingway"."""
    name = " ".join(name.split())
    if name.count(",") == 1:
        last, first = (p.strip() for p in name.split(","))
        if first and last and not re.search(r"\b(jr|sr|filho|neto)\.?$", first, re.IGNORECASE):
            return f"{first} {last}"
    return name


def read_metadata(h: Header, fallback_title: str) -> Metadata:
    md = Metadata()
    md.title = h.text(EXTH_TITLE) or h.full_name or fallback_title
    authors = []
    for value in h.texts(EXTH_AUTHOR):
        authors += [_author(a) for a in re.split(r"\s*(?:&|;)\s*", value) if a.strip()]
    md.authors = authors
    md.publisher = h.text(EXTH_PUBLISHER)
    md.description = html.unescape(re.sub(r"<[^>]+>", " ", h.text(EXTH_DESCRIPTION))).strip()
    md.isbn = re.sub(r"[^\dXx]", "", h.text(EXTH_ISBN))
    md.subjects = [s for s in h.texts(EXTH_SUBJECT) if s]
    md.date = h.text(EXTH_DATE)[:10]
    md.rights = h.text(EXTH_RIGHTS)
    md.language = h.text(EXTH_LANGUAGE) or "pt"
    return md


def kindle_info(path: Path) -> tuple[dict, bytes | None]:
    """Metadados, número de capítulos e capa, sem descomprimir o texto (para a estante)."""
    kf = KindleFile(Path(path).read_bytes())
    h = kf.header
    md = read_metadata(h, Path(path).stem)
    if h.version >= 8:
        chapters = len(read_index(kf.records, h.absolute(h.skel_index))[0])
    else:
        chapters = len(read_index(kf.records, h.absolute(h.ncx_index), h.encoding)[0])
    slots = kf.resource_slots()
    cover = None
    off = h.number(EXTH_COVER)
    if off is not None and off < len(slots) and slots[off] and _image_ext(slots[off]):
        cover = slots[off]
    else:
        cover = next((s for s in slots if s and _image_ext(s)), None)
    info = {"title": md.title, "authors": md.authors, "language": md.language, "pages": chapters,
            "kindle": "KF8 (AZW3)" if kf.kf8 is not None else "MOBI 6"}
    return info, cover


# ------------------------------------------------------------------ KF8
KINDLE_RES = re.compile(rb"kindle:(embed|flow):([0-9A-Va-v]{4})(?:\?mime=[\w/+.-]*)?")
KINDLE_POS = re.compile(rb"kindle:pos:fid:([0-9A-Va-v]{4}):off:([0-9A-Va-v]{10})")
ANCHOR_TAG = re.compile(rb"<[A-Za-z][^<>]*?\s(?:id|name|aid)\s*=\s*[\"'][^\"'<>]*[\"'][^<>]*>")
ATTR = {k: re.compile(rb"\s%s\s*=\s*[\"']([^\"'<>]*)[\"']" % k.encode()) for k in ("id", "name", "aid")}
AMAZON_ATTR = re.compile(r"^(aid|data-amzn.*)$", re.IGNORECASE)


@dataclass
class Part:
    start: int
    data: bytes
    href: str
    anchors: list[tuple[int, str, str]] = field(default_factory=list)  # (posição, tipo, valor)
    starts: list[int] = field(default_factory=list)
    needed_aids: set[str] = field(default_factory=set)

    def index_anchors(self) -> None:
        for m in ANCHOR_TAG.finditer(self.data):
            tag = m.group(0)
            for kind in ("id", "name", "aid"):
                a = ATTR[kind].search(tag)
                if a:
                    self.anchors.append((m.start(), kind, a.group(1).decode("utf-8", "replace")))
                    break
        self.starts = [a[0] for a in self.anchors]

    def anchor_at(self, pos: int) -> str:
        """Id do elemento com âncora mais próximo, no ponto ou antes dele."""
        i = bisect.bisect_right(self.starts, pos) - 1
        if i < 0:
            return ""
        _, kind, value = self.anchors[i]
        if kind == "aid":
            self.needed_aids.add(value)
            return f"aid-{value}"
        return value


class KF8Reader:
    def __init__(self, kf: KindleFile, book: Book, folder: Path, progress):
        self.kf, self.h, self.book, self.progress = kf, kf.header, book, progress
        self.folder = folder

    def read(self) -> None:
        h, records = self.h, self.kf.records
        text = self.kf.text(h, lambda f: self.progress(0.05 + 0.45 * f, "Descomprimindo o texto"))
        flows = [text]
        fdst = h.absolute(h.fdst_index)
        if fdst is not None and fdst < len(records) and records[fdst][:4] == b"FDST":
            count = _u32(records[fdst], 8)
            bounds = [struct.unpack_from(">LL", records[fdst], 12 + 8 * i) for i in range(count)]
            flows = [text[a:b] for a, b in bounds] or [text]
        self.progress(0.55, "Montando os capítulos")
        res = Resources(self.kf.resource_slots(), self.book, self.folder)
        flow_hrefs = self._flows(flows, res)
        self.frags = self._fragments()
        self.parts = self._parts(flows[0])
        for part in self.parts:
            part.index_anchors()

        def repl_res(m: re.Match) -> bytes:
            n = int(m.group(2), 32)
            href = res.href(n) if m.group(1) == b"embed" else flow_hrefs.get(n)
            return b"../" + href.encode() if href else b""

        rewritten = []
        for part in self.parts:
            data = KINDLE_RES.sub(repl_res, part.data)
            data = KINDLE_POS.sub(lambda m: self._link(int(m.group(1), 32), int(m.group(2), 32)).encode(), data)
            rewritten.append(data)
        self.book.toc = self._toc()
        for i, (part, data) in enumerate(zip(self.parts, rewritten)):
            self.book.documents.append(self._document(part, data))
            if i % 10 == 0:
                self.progress(0.6 + 0.35 * i / max(1, len(self.parts)), "Lendo capítulos")

    def _flows(self, flows: list[bytes], res: Resources) -> dict[int, str]:
        """CSS e SVG dos fluxos 1..n viram arquivos do livro."""
        hrefs = {}
        for n, flow in enumerate(flows[1:], start=1):
            head = flow[:400].lstrip()
            if head.startswith(b"<?xml") or b"<svg" in head:
                href, media = f"images/flow{n:04d}.svg", "image/svg+xml"
            else:
                href, media = f"styles/flow{n:04d}.css", "text/css"
            data = KINDLE_RES.sub(lambda m: (b"../" + (res.href(int(m.group(2), 32)) or "").encode())
                                  if m.group(1) == b"embed" else m.group(0), flow)
            path = self.folder / href.replace("/", "_")
            path.write_bytes(data)
            self.book.add_resource(Resource(href, media, path))
            hrefs[n] = href
        return hrefs

    def _fragments(self) -> list[tuple[int, int, str]]:
        """(posição de inserção, tamanho, aid do seletor) de cada fragmento."""
        entries, cncx = read_index(self.kf.records, self.h.absolute(self.h.frag_index))
        frags = []
        for label, tags in entries:
            try:
                insert = int(label)
            except ValueError:
                insert = 0
            selector = cncx.get((tags.get(2) or [-1])[0], "")
            aid = re.search(r"aid='([^']+)'", selector)
            length = tags[6][1] if len(tags.get(6, [])) > 1 else 0
            frags.append((insert, length, aid.group(1) if aid else ""))
        return frags

    def _parts(self, main: bytes) -> list[Part]:
        skels, _ = read_index(self.kf.records, self.h.absolute(self.h.skel_index))
        if not skels:  # sem esqueletos: o texto inteiro é um arquivo só
            return [Part(0, main, "text/part0000.xhtml")]
        parts, fi = [], 0
        for n, (_, tags) in enumerate(skels):
            count = (tags.get(1) or [0])[0]
            pos, length = (tags.get(6) or [0, 0])[:2]
            skeleton = main[pos:pos + length]
            base = pos + length
            for _ in range(count):
                if fi >= len(self.frags):
                    break
                insert, flen, aid = self.frags[fi]
                piece = main[base:base + flen]
                at = insert - pos
                head = skeleton[:at] if 0 <= at <= len(skeleton) else b""
                if not 0 <= at <= len(skeleton) or head.rfind(b"<") > head.rfind(b">"):
                    at = self._insert_point(skeleton, aid)  # posição errada: acha a tag pelo aid
                skeleton = skeleton[:at] + piece + skeleton[at:]
                base += flen
                fi += 1
            parts.append(Part(pos, skeleton, f"text/part{n:04d}.xhtml"))
        return parts

    @staticmethod
    def _insert_point(skeleton: bytes, aid: str) -> int:
        m = re.search(rb"<[^>]*\said\s*=\s*[\"']%s[\"'][^>]*>" % re.escape(aid.encode()), skeleton) if aid else None
        if m:
            return m.end()
        body_end = skeleton.rfind(b"</body>")
        return body_end if body_end >= 0 else len(skeleton)

    def _locate(self, pos: int) -> tuple[Part | None, str]:
        for part in self.parts:
            if part.start <= pos < part.start + len(part.data):
                return part, part.anchor_at(pos - part.start)
        return None, ""

    def _target(self, fid: int, off: int) -> str:
        """``kindle:pos:fid`` → "text/partNNNN.xhtml#id" (relativo ao livro)."""
        if fid >= len(self.frags):
            return ""
        part, anchor = self._locate(self.frags[fid][0] + off)
        if part is None:
            return ""
        return f"{part.href}#{anchor}" if anchor else part.href

    def _link(self, fid: int, off: int) -> str:
        target = self._target(fid, off)
        return target.split("/", 1)[1] if target else "#"  # todos os capítulos ficam em text/

    def _toc(self) -> list[TocEntry]:
        entries, cncx = read_index(self.kf.records, self.h.absolute(self.h.ncx_index))
        nodes = []
        for _, tags in entries:
            title = " ".join(cncx.get((tags.get(3) or [-1])[0], "").split())
            if len(tags.get(6, [])) >= 2:
                fid, off = tags[6][:2]
                href = self._target(fid, off)
                pos = self.frags[fid][0] + off if fid < len(self.frags) else 0
            else:
                pos = (tags.get(1) or [0])[0]
                part, anchor = self._locate(pos)
                href = (f"{part.href}#{anchor}" if anchor else part.href) if part else ""
            nodes.append({"title": title or "Sem título", "href": href, "pos": pos,
                          "depth": (tags.get(4) or [0])[0], "parent": (tags.get(21) or [None])[0]})
        return build_toc(nodes)

    def _document(self, part: Part, data: bytes) -> Document:
        root = parse_xhtml(data)
        strip_xhtml_ns(root)
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            aid = el.get("aid")
            if aid and aid in part.needed_aids and not el.get("id"):
                el.set("id", f"aid-{aid}")
            if el.tag == "a" and el.get("name") and not el.get("id"):
                el.set("id", el.get("name"))
            for attr in [a for a in el.attrib if AMAZON_ATTR.match(a)]:
                del el.attrib[attr]
        doc = document_from_root(root, part.href, f"p{len(self.book.documents):04d}")
        fixed = self.book.layout == "fixed"
        if fixed and doc.viewport is None:
            doc.viewport = self.book.viewport
        return doc


def build_toc(nodes: list[dict]) -> list[TocEntry]:
    """Árvore do sumário pelos pais (tag 21) ou, sem eles, pela profundidade."""
    entries = [TocEntry(n["title"], n["href"]) for n in nodes]
    order = sorted(range(len(nodes)), key=lambda i: (nodes[i]["pos"], i))
    roots: list[TocEntry] = []
    if any(n["parent"] is not None for n in nodes):
        for i in order:
            p = nodes[i]["parent"]
            (entries[p].children if p is not None and 0 <= p < len(entries) and p != i else roots).append(entries[i])
    else:
        stack: list[tuple[int, TocEntry]] = []
        for i in order:
            depth = nodes[i]["depth"]
            while stack and stack[-1][0] >= depth:
                stack.pop()
            (stack[-1][1].children if stack else roots).append(entries[i])
            stack.append((depth, entries[i]))

    def prune(items: list[TocEntry]) -> list[TocEntry]:  # entradas sem destino cedem lugar aos filhos
        out = []
        for e in items:
            e.children = prune(e.children)
            out.extend([e] if e.href else e.children)
        return out
    return prune(roots)


# ------------------------------------------------------------------ MOBI 6
FILEPOS = re.compile(rb"filepos\s*=\s*[\"']?0*(\d+)[\"']?", re.IGNORECASE)
RECINDEX = re.compile(rb"(?:hi|lo)?recindex\s*=\s*[\"']?(\d+)[\"']?", re.IGNORECASE)
PAGEBREAK = re.compile(rb"<mbp:pagebreak[^>]*>(?:\s*</mbp:pagebreak>)?", re.IGNORECASE)
FONT_SIZES = {"1": "0.63em", "2": "0.82em", "3": "1em", "4": "1.13em", "5": "1.5em", "6": "2em", "7": "3em"}
BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li"}


def _insert_anchors(raw: bytes, positions: list[int]) -> bytes:
    """Âncora ``filepos{N}`` em cada posição de destino (fora de qualquer tag)."""
    out, last = [], 0
    for p in sorted(set(positions)):
        if not 0 < p < len(raw):
            continue
        at = p
        if raw.rfind(b"<", 0, p) > raw.rfind(b">", 0, p):  # cai dentro de uma tag: vai para depois dela
            close = raw.find(b">", p)
            at = close + 1 if close >= 0 else p
        at = max(at, last)
        out += [raw[last:at], b'<a id="filepos%d"></a>' % p]
        last = at
    out.append(raw[last:])
    return b"".join(out)


class Mobi6Reader:
    def __init__(self, kf: KindleFile, book: Book, folder: Path, progress):
        self.kf, self.h, self.book, self.progress, self.folder = kf, kf.mobi6, book, progress, folder

    def read(self) -> None:
        h = self.h
        raw = self.kf.text(h, lambda f: self.progress(0.05 + 0.5 * f, "Descomprimindo o texto"))
        if not h.is_mobi:  # PalmDOC (TEXtREAd): texto puro
            self._plain_text(raw.decode("cp1252", "replace"))
            return
        ncx, cncx = read_index(self.kf.records, h.absolute(h.ncx_index), h.encoding)
        guide_toc = re.search(rb"<reference[^>]*type\s*=\s*[\"']?toc[^>]*>", raw[:raw.find(b"<body") + 1 or 4096],
                              re.IGNORECASE)
        toc_page = FILEPOS.search(guide_toc.group(0)) if guide_toc else None
        positions = [int(m.group(1)) for m in FILEPOS.finditer(raw)]
        positions += [(t.get(1) or [0])[0] for _, t in ncx]
        raw = _insert_anchors(raw, positions)
        start = re.search(rb"<body[^>]*>", raw, re.IGNORECASE)
        end = raw.rfind(b"</body>")
        raw = raw[start.end() if start else 0:end if end > 0 else len(raw)]
        res = Resources(self.kf.resource_slots(), self.book, self.folder)
        raw = FILEPOS.sub(lambda m: b'href="#filepos%s"' % m.group(1), raw)
        raw = RECINDEX.sub(lambda m: b'src="../%s"' % (res.href(int(m.group(1))) or "").encode(), raw)

        self.progress(0.6, "Separando os capítulos")
        chunks, pending = [], b""
        for chunk in PAGEBREAK.split(raw):
            chunk = pending + chunk
            visible = re.sub(rb"<(?!img)[^>]*>|\s|&nbsp;", b"", chunk)
            if not visible:  # capítulo vazio (só âncoras): junta com o seguinte
                pending = chunk
                continue
            chunks.append(chunk)
            pending = b""
        if pending and chunks:
            chunks[-1] += pending
        trees = []
        where: dict[str, str] = {}
        for n, chunk in enumerate(chunks or [b""]):
            href = f"text/part{n:04d}.xhtml"
            body = lhtml.document_fromstring(b"<html><body>" + chunk + b"</body></html>",
                                             parser=lhtml.HTMLParser(encoding=h.encoding)).find("body")
            self._clean(body)
            for el in body.iter():
                if isinstance(el.tag, str) and el.get("id"):
                    where.setdefault(el.get("id"), href)
            trees.append((href, body))
        for n, (href, body) in enumerate(trees):
            for a in body.iter("a"):
                target = (a.get("href") or "")[1:] if (a.get("href") or "").startswith("#") else None
                if target:
                    a.set("href", f"{where[target].split('/', 1)[1]}#{target}" if target in where else "#")
            self.book.documents.append(Document(href=href, title="", body=inner_xml(body), id=f"p{n:04d}"))
        self.book.toc = self._toc(ncx, cncx, where, toc_page)

    def _clean(self, body) -> None:
        """Atributos do HTML do MOBI 6 viram CSS; tags ``mbp:`` saem."""
        etree.strip_tags(body, *{el.tag for el in body.iter() if isinstance(el.tag, str) and ":" in el.tag},
                         etree.Comment, etree.ProcessingInstruction)
        for el in list(body.iter()):
            if not isinstance(el.tag, str):
                continue
            style = []
            if el.tag in BLOCKS:
                for attr, prop in (("height", "margin-top"), ("width", "text-indent"), ("align", "text-align")):
                    value = el.attrib.pop(attr, None)
                    if value:
                        style.append(f"{prop}: {value}")
            if el.tag == "font":
                el.tag = "span"
                if el.get("size") in FONT_SIZES:
                    style.append(f"font-size: {FONT_SIZES[el.get('size')]}")
                if el.get("color"):
                    style.append(f"color: {el.get('color')}")
                if el.get("face"):
                    style.append(f"font-family: {el.get('face')}")
                for attr in ("size", "color", "face"):
                    el.attrib.pop(attr, None)
            elif el.tag == "center":
                el.tag = "div"
                style.append("text-align: center")
            elif el.tag == "img" and not el.get("src"):
                el.drop_tree()
                continue
            for attr in [a for a in el.attrib if not re.fullmatch(r"[A-Za-z_][\w.-]*", a)]:
                del el.attrib[attr]  # atributos que não existem em XML
            if style:
                el.set("style", "; ".join(filter(None, [el.get("style", "").rstrip("; "), *style])))

    def _toc(self, ncx, cncx, where: dict[str, str], toc_page) -> list[TocEntry]:
        nodes = []
        for _, tags in ncx:
            pos = (tags.get(1) or [0])[0]
            anchor = f"filepos{pos}"
            href = f"{where[anchor]}#{anchor}" if anchor in where else ""
            nodes.append({"title": " ".join(cncx.get((tags.get(3) or [-1])[0], "").split()) or "Sem título",
                          "href": href, "pos": pos, "depth": (tags.get(4) or [0])[0],
                          "parent": (tags.get(21) or [None])[0]})
        toc = build_toc(nodes)
        if toc or toc_page is None:
            return toc
        anchor = f"filepos{int(toc_page.group(1))}"  # sumário em HTML apontado pelo guia
        doc = self.book.document(where.get(anchor, ""))
        if doc is None:
            return []
        out = []
        for m in re.finditer(r'<a [^>]*href="([^"#]*#filepos\d+)"[^>]*>(.*?)</a>', doc.body, re.DOTALL):
            title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())
            if title:
                out.append(TocEntry(title, f"text/{m.group(1)}"))
        return out

    def _plain_text(self, text: str) -> None:
        paragraphs = [p.strip() for p in re.split(r"\r?\n\s*\r?\n|\r?\n(?=\s)", text) if p.strip()]
        chunk, size, n = [], 0, 0
        for i, p in enumerate(paragraphs):
            chunk.append(f"<p>{html.escape(' '.join(p.split()))}</p>")
            size += len(p)
            if size > 100_000 or i == len(paragraphs) - 1:
                self.book.documents.append(Document(href=f"text/part{n:04d}.xhtml", title="",
                                                    body="\n".join(chunk), id=f"p{n:04d}"))
                chunk, size, n = [], 0, n + 1


# ------------------------------------------------------------------ plugin
class MobiInput(InputPlugin):
    format = "mobi"

    def recommendations(self) -> list[Recommendation]:
        try:
            with open(self.path, "rb") as fh:
                fixed = b"fixed-layout" in fh.read(65536) and self._is_fixed()
        except OSError:
            fixed = False
        return [Recommendation("mode", "fixed" if fixed else "reflow", Level.MED)]

    def _is_fixed(self) -> bool:
        try:
            h = KindleFile(Path(self.path).read_bytes()).header
        except ConversionError:
            return False
        return h.text(EXTH_FIXED_LAYOUT).lower() == "true"

    def read(self, ctx: InputContext) -> Book:
        ctx.progress(0.02, "Lendo o livro Kindle")
        kf = KindleFile(Path(ctx.source).read_bytes())
        book = Book(source_format="mobi")
        book.metadata = read_metadata(kf.header, Path(ctx.original_name).stem)
        h = kf.header
        if h.text(EXTH_FIXED_LAYOUT).lower() == "true":
            size = re.match(r"(\d+)\s*x\s*(\d+)", h.text(EXTH_RESOLUTION))
            book.layout = "fixed"
            book.viewport = (int(size.group(1)), int(size.group(2))) if size else None
        folder = ctx.ws.sub("kindle")
        if kf.kf8 is not None:
            KF8Reader(kf, book, folder, ctx.progress).read()
            book.notes.append("Livro Kindle lido da versão KF8 (AZW3), com o CSS original.")
        else:
            Mobi6Reader(kf, book, folder, ctx.progress).read()
            book.notes.append("Livro Kindle no formato antigo (MOBI 6): a formatação é a que o MOBI guarda.")
        if not book.documents:
            raise ConversionError("O livro Kindle não tem texto que dê para ler.")
        off = h.number(EXTH_COVER)
        cover = book.resource(next((r.href for r in book.resources if r.href.startswith(f"images/img{off + 1:05d}.")),
                                   "")) if off is not None else None
        if cover is not None:
            cover.properties.add("cover-image")
            book.cover = cover
        if book.layout == "fixed" and book.viewport:
            for doc in book.documents:
                doc.viewport = doc.viewport or book.viewport
        if ctx.opts.mode == "fixed" and book.layout != "fixed":
            return self._as_pages(ctx, book)
        if ctx.opts.apply_to_images or book.layout == "fixed":
            adjust_book_images(ctx, book)
        return book

    def _as_pages(self, ctx: InputContext, book: Book) -> Book:
        """Layout fiel: grava um EPUB intermediário e deixa o leitor de EPUB paginar."""
        from dataclasses import replace

        from ..outputs.epub import write_epub
        from .epub import EpubInput

        tmp = ctx.ws.sub("paginar") / "livro.epub"
        write_epub(book, tmp)
        return EpubInput(tmp).read(replace(ctx, source=tmp))
