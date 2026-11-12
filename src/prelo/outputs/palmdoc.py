"""Estruturas binárias comuns a MOBI 6 e KF8 (AZW3).

Referências públicas do formato: wiki MobileRead (MOBI, KF8), KindleUnpack e
o leitor do Calibre, usados também para validar os arquivos nos testes.

* PalmDB: o contêiner (cabeçalho de 78 bytes + tabela de registros).
* PalmDOC: compressão LZ77 simples, registros de texto de 4096 bytes.
* EXTH: metadados (autor, capa, layout fixo...).
* INDX/TAGX/CNCX: índices (sumário, esqueletos e fragmentos do KF8).
"""

from __future__ import annotations

import struct
import time
import unicodedata
from collections import namedtuple
from typing import Iterable

RECORD_SIZE = 4096
NULL = 0xFFFFFFFF
BASE32 = "0123456789ABCDEFGHIJKLMNOPQRSTUV"
FLIS = (b"FLIS\0\0\0\x08\0\x41\0\0\0\0\0\0\xff\xff\xff\xff\0\x01\0\x03\0\0\0\x03\0\0\0\x01" + b"\xff" * 4)
EOF_RECORD = b"\xe9\x8e\r\n"


# ------------------------------------------------------------- números
def to_base32(num: int, digits: int = 0) -> str:
    out = ""
    while True:
        num, rem = divmod(num, 32)
        out = BASE32[rem] + out
        if num == 0:
            break
    return out.rjust(digits, "0")


def encint(value: int, forward: bool = True) -> bytes:
    """Inteiro de largura variável (7 bits por byte). ``forward``: o bit 8 fica
    no último byte, que é o que os índices INDX e o CNCX usam."""
    if value < 0:
        raise ValueError("vwi negativo")
    out = bytearray()
    while True:
        out.append(value & 0x7F)
        value >>= 7
        if value == 0:
            break
    out[0 if forward else -1] |= 0x80
    out.reverse()
    return bytes(out)


def decint(data: bytes, pos: int = 0) -> tuple[int, int]:
    """Lê um inteiro ``forward``. Devolve (valor, bytes consumidos)."""
    value = consumed = 0
    while True:
        b = data[pos + consumed]
        consumed += 1
        value = (value << 7) | (b & 0x7F)
        if b & 0x80:
            return value, consumed


def align4(raw: bytes) -> bytes:
    return raw + b"\0" * ((4 - len(raw) % 4) % 4)


# ------------------------------------------------------------ PalmDOC
def compress_doc(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        if i > 10 and n - i > 10:
            found = False
            for length in range(10, 2, -1):
                chunk = data[i:i + length]
                pos = data.rfind(chunk, max(0, i - 2047), i)
                if pos >= 0 and (i - pos) <= 2047:
                    out += struct.pack(">H", 0x8000 + (((i - pos) << 3) & 0x3FF8) + (length - 3))
                    i += length
                    found = True
                    break
            if found:
                continue
        ch = data[i]
        i += 1
        if ch == 0x20 and i < n and 0x40 <= data[i] < 0x80:
            out.append(data[i] ^ 0x80)
            i += 1
            continue
        if ch == 0 or 9 <= ch < 0x80:
            out.append(ch)
            continue
        j = i
        seq = bytearray([ch])
        while j < n and len(seq) < 8:
            c = data[j]
            if c == 0 or 9 <= c < 0x80:
                break
            seq.append(c)
            j += 1
        out.append(len(seq))
        out += seq
        i += len(seq) - 1
    return bytes(out)


def decompress_doc(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if 1 <= c <= 8:
            out += data[i:i + c]
            i += c
        elif c < 0x80:
            out.append(c)
        elif c >= 0xC0:
            out.append(0x20)
            out.append(c ^ 0x80)
        else:
            c = (c << 8) | data[i]
            i += 1
            dist, length = (c >> 3) & 0x7FF, (c & 7) + 3
            for _ in range(length):
                out.append(out[-dist])
    return bytes(out)


def text_records(text: bytes, compress: bool = True) -> list[bytes]:
    """Divide o texto em registros de 4096 bytes. Se um registro termina no
    meio de um caractere UTF-8, os bytes que faltam vão no fim do registro
    seguidos de um byte com a quantidade (entrada "multibyte", flag 0b1)."""
    records = []
    pos, n = 0, len(text)
    while pos < n:
        end = min(pos + RECORD_SIZE, n)
        chunk = text[pos:end]
        overlap = b""
        if end < n:
            # quantos bytes do próximo registro completam o último caractere?
            k = len(chunk) - 1
            while k >= 0 and (chunk[k] & 0xC0) == 0x80:
                k -= 1
            if k >= 0:
                lead = chunk[k]
                need = 2 if lead >= 0xC0 else 1
                need = 3 if lead >= 0xE0 else need
                need = 4 if lead >= 0xF0 else need
                have = len(chunk) - k
                if lead >= 0xC0 and have < need:
                    overlap = text[end:end + need - have]
        data = compress_doc(chunk) if compress else chunk
        records.append(data + overlap + bytes([len(overlap)]))
        pos = end
    return records


# ---------------------------------------------------------------- EXTH
def exth_block(records: Iterable[tuple[int, bytes | int]]) -> bytes:
    body = bytearray()
    count = 0
    for code, val in records:
        data = struct.pack(">I", val) if isinstance(val, int) else val
        body += struct.pack(">II", code, len(data) + 8) + data
        count += 1
    return align4(b"EXTH" + struct.pack(">II", 12 + len(body), count) + bytes(body))


# --------------------------------------------------------------- PalmDB
def palmdb_name(title: str) -> bytes:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    ascii_title = "".join(c if c.isalnum() else "_" for c in ascii_title).strip("_") or "Livro"
    raw = ascii_title.encode("ascii")
    if len(raw) > 31:
        raw = raw[:14] + b"-" + raw[-16:]
    return raw.ljust(32, b"\0")


def palmdb(title: str, records: list[bytes]) -> bytes:
    now = int(time.time())
    n = len(records)
    header = palmdb_name(title) + struct.pack(
        ">HHIIIIII4s4sIIH", 0, 0, now, now, 0, 0, 0, 0, b"BOOK", b"MOBI", 2 * n - 1, 0, n)
    offset = 78 + 8 * n + 2
    table = bytearray()
    for i, rec in enumerate(records):
        table += struct.pack(">I", offset) + b"\0" + struct.pack(">I", 2 * i)[1:]
        offset += len(rec)
    return header + bytes(table) + b"\0\0" + b"".join(records)


def fcis(text_length: int, kf8: bool) -> bytes:
    if kf8:
        return (b"FCIS\x00\x00\x00\x14\x00\x00\x00\x10\x00\x00\x00\x02\x00\x00\x00\x00"
                + struct.pack(">I", text_length)
                + b"\x00\x00\x00\x00\x00\x00\x00\x28\x00\x00\x00\x00\x00\x00\x00\x28"
                  b"\x00\x00\x00\x08\x00\x01\x00\x01\x00\x00\x00\x00")
    return (b"FCIS\x00\x00\x00\x14\x00\x00\x00\x10\x00\x00\x00\x01\x00\x00\x00\x00"
            + struct.pack(">I", text_length)
            + b"\x00\x00\x00\x00\x00\x00\x00\x20\x00\x00\x00\x08\x00\x01\x00\x01\x00\x00\x00\x00")


# ------------------------------------------------------------- idioma
_LANG = {"pt": 22, "en": 9, "es": 10, "fr": 12, "de": 7, "it": 16, "nl": 19, "ja": 17, "zh": 4,
         "ru": 25, "pl": 21, "ca": 3, "gl": 86, "eu": 45, "sv": 29, "da": 6, "fi": 11, "no": 20,
         "cs": 5, "el": 8, "he": 13, "hu": 14, "tr": 31, "ko": 18, "ar": 1, "ro": 24, "uk": 34}
# dialeto já deslocado (sublíngua << 2), como na tabela IANA→MOBI do Calibre
_DIALECT = {("pt", "BR"): 4, ("pt", "PT"): 8, ("en", "US"): 4, ("en", "GB"): 8, ("en", "AU"): 12,
            ("en", "CA"): 16, ("es", "ES"): 4, ("es", "MX"): 8, ("fr", "FR"): 4, ("fr", "CA"): 12,
            ("de", "DE"): 4, ("it", "IT"): 4}


def mobi_language(tag: str) -> int:
    parts = (tag or "").replace("_", "-").split("-")
    lang = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 else ""
    return (_DIALECT.get((lang, region), 0) << 8) | _LANG.get(lang, 0)


# ------------------------------------------------------------- índices
TagMeta = namedtuple("TagMeta", "name number values_per_entry bitmask end_flag")
END_TAG = TagMeta("eof", 0, 0, 0, 1)
_MASK_SHIFT = {1: 0, 2: 1, 3: 0, 4: 2, 8: 3, 12: 2, 16: 4, 32: 5, 48: 4, 64: 6, 128: 7, 192: 6}
INDEX_HEADER_LENGTH = 192


class CNCX:
    """Registros de texto dos índices: <tamanho vwi><utf-8>."""

    MAX = 500

    def __init__(self, strings: Iterable[str] = ()):
        self.offsets: dict[str, int] = {}
        self.records: list[bytes] = []
        buf = bytearray()
        limit = 0x10000 - 1024
        for s in strings:
            if s in self.offsets:
                continue
            raw = s[: self.MAX].encode("utf-8")
            entry = encint(len(raw)) + raw
            if len(buf) + len(entry) > limit:
                self.records.append(align4(bytes(buf)))
                buf = bytearray()
            self.offsets[s] = len(self.records) * 0x10000 + len(buf)
            buf += entry
        if buf:
            self.records.append(align4(bytes(buf)))

    def __getitem__(self, s: str) -> int:
        return self.offsets[s]


def _tagx(tag_types: tuple[TagMeta, ...], control_byte_count: int) -> bytes:
    body = bytearray()
    for t in tag_types:
        body += bytes([t.number, t.values_per_entry, t.bitmask, t.end_flag])
    return b"TAGX" + struct.pack(">II", 12 + len(body), control_byte_count) + bytes(body)


def _control_bytes(tags: dict, tag_types: tuple[TagMeta, ...]) -> bytes:
    cbs, ans = [], 0
    for t in tag_types:
        if t.end_flag:
            cbs.append(ans)
            ans = 0
            continue
        vals = tags.get(t.name)
        if vals is None:
            continue
        nvals = len(vals) if isinstance(vals, (list, tuple)) else 1
        ans |= t.bitmask & ((nvals // t.values_per_entry) << _MASK_SHIFT[t.bitmask])
    return bytes(cbs)


def build_index(entries: list[tuple[bytes, dict]], tag_types: tuple[TagMeta, ...],
                cncx: CNCX | None = None) -> list[bytes]:
    """Monta [registro de cabeçalho, registros de entradas..., registros CNCX]."""
    blocks, idxts, counts, last_keys = [bytearray()], [bytearray()], [0], [b""]
    limit = 0x10000 - INDEX_HEADER_LENGTH - 1048
    for key, tags in entries:
        raw = bytearray([len(key)]) + key + _control_bytes(tags, tag_types)
        for t in tag_types:
            vals = tags.get(t.name)
            if vals is None or t.end_flag:
                continue
            for v in (vals if isinstance(vals, (list, tuple)) else [vals]):
                raw += encint(int(v))
        if len(blocks[-1]) + len(idxts[-1]) + len(raw) + 2 > limit:
            blocks.append(bytearray())
            idxts.append(bytearray())
            counts.append(0)
            last_keys.append(b"")
        idxts[-1] += struct.pack(">H", INDEX_HEADER_LENGTH + len(blocks[-1]))
        blocks[-1] += raw
        counts[-1] += 1
        last_keys[-1] = key

    records = []
    for block, idxt, count in zip(blocks, idxts, counts):
        block_b = align4(bytes(block))
        idxt_b = align4(b"IDXT" + bytes(idxt))
        header = (b"INDX" + struct.pack(">I", INDEX_HEADER_LENGTH) + b"\0" * 4 + struct.pack(">I", 1)
                  + b"\0" * 4 + struct.pack(">I", INDEX_HEADER_LENGTH + len(block_b))
                  + struct.pack(">I", count) + b"\xff" * 8 + b"\0" * 156)
        assert len(header) == INDEX_HEADER_LENGTH
        records.append(header + block_b + idxt_b)

    tagx = align4(_tagx(tag_types, 1))
    geometry = bytearray()
    idxt = bytearray(b"IDXT")
    pos = INDEX_HEADER_LENGTH + len(tagx)
    for key, count in zip(last_keys, counts):
        idxt += struct.pack(">H", pos + len(geometry))
        geometry += bytes([len(key)]) + key + struct.pack(">H", count)
    geometry_b = align4(bytes(geometry))
    idxt_offset = INDEX_HEADER_LENGTH + len(tagx) + len(geometry_b)
    head = bytearray(b"INDX")
    head += struct.pack(">I", INDEX_HEADER_LENGTH)
    head += b"\0" * 8
    head += struct.pack(">I", 2)
    head += struct.pack(">I", idxt_offset)
    head += struct.pack(">I", len(records))
    head += struct.pack(">I", 65001)
    head += struct.pack(">I", NULL)
    head += struct.pack(">I", sum(counts))
    head += struct.pack(">III", 0, 0, 0)
    head += struct.pack(">I", len(cncx.records) if cncx else 0)
    head += b"\0" * 124
    head += struct.pack(">I", INDEX_HEADER_LENGTH)
    head += b"\0" * 8
    assert len(head) == INDEX_HEADER_LENGTH
    header_record = bytes(head) + tagx + geometry_b + align4(bytes(idxt))
    return [header_record] + records + (list(cncx.records) if cncx else [])
