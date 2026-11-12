"""Leitor mínimo de MOBI/AZW3, só para os testes.

Confere a estrutura sem depender do Calibre: cabeçalhos, descompressão dos
registros de texto (removendo os bytes extras do fim de cada registro) e,
no KF8, a remontagem de cada arquivo a partir do esqueleto e dos fragmentos.
"""

from __future__ import annotations

import struct

from prelo.outputs.palmdoc import decint, decompress_doc


def records(data: bytes) -> list[bytes]:
    n = struct.unpack(">H", data[76:78])[0]
    offsets = [struct.unpack(">I", data[78 + 8 * i: 82 + 8 * i])[0] for i in range(n)]
    offsets.append(len(data))
    return [data[offsets[i]:offsets[i + 1]] for i in range(n)]


def strip_trailing(rec: bytes, flags: int) -> bytes:
    f = flags >> 1
    while f:
        if f & 1:
            size, _ = _backward(rec)
            rec = rec[:-size]
        f >>= 1
    if flags & 1:
        rec = rec[: -((rec[-1] & 3) + 1)]
    return rec


def _backward(rec: bytes) -> tuple[int, int]:
    val, n = 0, 0
    bs = []
    for b in reversed(rec):
        bs.append(b & 0x7F)
        n += 1
        if b & 0x80:
            break
    for b in reversed(bs):
        val = (val << 7) | b
    return val, n


class KindleBook:
    def __init__(self, data: bytes):
        self.data = data
        self.recs = records(data)
        r0 = self.recs[0]
        self.compression, _, self.text_length, self.text_count = struct.unpack(">HHIH", r0[:10])
        self.encryption = struct.unpack(">H", r0[12:14])[0]
        assert r0[16:20] == b"MOBI"
        self.header_length = struct.unpack(">I", r0[20:24])[0]
        self.version = struct.unpack(">I", r0[36:40])[0]
        self.extra_flags = struct.unpack(">I", r0[240:244])[0] if self.header_length >= 0xE4 else 0
        self.exth = self._exth(r0)
        title_off, title_len = struct.unpack(">II", r0[84:92])
        self.title = r0[title_off:title_off + title_len].decode("utf-8")

    def _exth(self, r0: bytes) -> dict[int, list[bytes]]:
        start = 16 + self.header_length
        assert r0[start:start + 4] == b"EXTH"
        _length, count = struct.unpack(">II", r0[start + 4:start + 12])
        pos = start + 12
        out: dict[int, list[bytes]] = {}
        for _ in range(count):
            code, size = struct.unpack(">II", r0[pos:pos + 8])
            out.setdefault(code, []).append(r0[pos + 8:pos + size])
            pos += size
        return out

    def text(self) -> bytes:
        parts = []
        for rec in self.recs[1:1 + self.text_count]:
            raw = strip_trailing(rec, self.extra_flags)
            parts.append(decompress_doc(raw) if self.compression == 2 else raw)
        out = b"".join(parts)
        assert len(out) == self.text_length, (len(out), self.text_length)
        return out

    def u32(self, off: int) -> int:
        return struct.unpack(">I", self.recs[0][off:off + 4])[0]

    # ---------------------------------------------------------------- KF8
    def index_entries(self, rec_no: int) -> list[tuple[bytes, dict]]:
        """Decodifica um índice INDX (TAGX + registros de entradas)."""
        head = self.recs[rec_no]
        assert head[:4] == b"INDX"
        nrecs = struct.unpack(">I", head[24:28])[0]
        tagx_off = struct.unpack(">I", head[180:184])[0]
        assert head[tagx_off:tagx_off + 4] == b"TAGX"
        tlen, cbc = struct.unpack(">II", head[tagx_off + 4:tagx_off + 12])
        tags = [tuple(head[tagx_off + 12 + 4 * k: tagx_off + 16 + 4 * k]) for k in range((tlen - 12) // 4)]
        out = []
        for r in range(rec_no + 1, rec_no + 1 + nrecs):
            rec = self.recs[r]
            idxt = struct.unpack(">I", rec[20:24])[0]
            count = struct.unpack(">I", rec[24:28])[0]
            pos_list = [struct.unpack(">H", rec[idxt + 4 + 2 * k: idxt + 6 + 2 * k])[0] for k in range(count)]
            for p in pos_list:
                klen = rec[p]
                key = rec[p + 1:p + 1 + klen]
                cb = rec[p + 1 + klen: p + 1 + klen + cbc]
                q = p + 1 + klen + cbc
                vals: dict[int, list[int]] = {}
                for tag, vpe, mask, end in tags:
                    if end:
                        continue
                    v = cb[0] & mask
                    if not v:
                        continue
                    shift = (mask & -mask).bit_length() - 1
                    nentries = v >> shift
                    lst = []
                    for _ in range(nentries * vpe):
                        val, used = decint(rec, q)
                        q += used
                        lst.append(val)
                    vals[tag] = lst
                out.append((key, vals))
        return out

    def kf8_files(self) -> list[str]:
        """Remonta cada XHTML a partir do esqueleto + fragmentos."""
        text = self.text()
        fdst = self.recs[self.u32(192)]
        assert fdst[:4] == b"FDST"
        flow0_end = struct.unpack(">I", fdst[16:20])[0]
        flow0 = text[:flow0_end]
        skel = self.index_entries(self.u32(252))
        frags = self.index_entries(self.u32(248))
        files = []
        fi = 0
        for _key, vals in skel:
            count = vals[1][0]
            start, length = vals[6][0], vals[6][1]
            skeleton = flow0[start:start + length]
            base = start + length
            for _ in range(count):
                fkey, fvals = frags[fi]
                insert = int(fkey) - start
                flen = fvals[6][1]
                chunk = flow0[base:base + flen]
                skeleton = skeleton[:insert] + chunk + skeleton[insert:]
                base += flen
                fi += 1
            files.append(skeleton.decode("utf-8"))
        return files
