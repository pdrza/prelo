"""Trailing byte sequences (TBS) do KF8.

No fim de cada registro de texto vai uma sequência de bytes dizendo quais
entradas do sumário começam, terminam ou atravessam aquele registro. O Kindle
usa isso para pular de capítulo em capítulo. O algoritmo segue o que o
KindleGen faz, conforme documentado no Calibre (``writer8/tbs.py``), e é o
mesmo que o inspetor de MOBI do Calibre usa para conferir os arquivos.
"""

from __future__ import annotations

from collections import OrderedDict, namedtuple

from .palmdoc import encint

Entry = namedtuple("Entry", "index start length depth parent first_child last_child title "
                            "action start_offset length_offset text_record_length")


class NegativeStrandIndex(Exception):
    pass


def encode_trailing_data(raw: bytes) -> bytes:
    """``<dados><tamanho>``: o tamanho é um vwi "para trás" que inclui a si mesmo."""
    lsize = 1
    while True:
        encoded = encint(len(raw) + lsize, forward=False)
        if len(encoded) == lsize:
            return raw + encoded
        lsize += 1


def _encode_fvwi(val: int, flags: int, flag_size: int) -> bytes:
    ans = val << flag_size
    for i in range(flag_size):
        ans |= flags & (1 << i)
    return encint(ans)


def encode_tbs(val: int, extra: dict, flag_size: int = 4) -> bytes:
    flags = 0
    for flag in extra:
        flags |= flag
    ans = _encode_fvwi(val, flags, flag_size)
    if 0b0010 in extra:
        ans += encint(extra[0b0010])
    if 0b0100 in extra:
        ans += bytes([extra[0b0100]])
    if 0b0001 in extra:
        ans += encint(extra[0b0001])
    return ans


def _fill(entry: Entry, start_offset: int, rec_len: int) -> Entry:
    length_offset = start_offset + entry.length
    if start_offset < 0:
        action = "spans" if length_offset > rec_len else "ends"
    else:
        action = "starts" if length_offset > rec_len else "completes"
    return entry._replace(action=action, start_offset=start_offset, length_offset=length_offset,
                          text_record_length=rec_len)


def _populate_strand(parent: Entry, entries: list[Entry]) -> list[Entry]:
    ans = [parent]
    children = [c for c in entries if c.parent == parent.index]
    if children:
        child = children[0]
        entries.remove(child)
        ans += _populate_strand(child, entries)
    else:
        siblings = [c for c in entries if c.depth == parent.depth and c.parent == parent.parent]
        for child in siblings:
            entries.remove(child)
            ans.append(child)
    return ans


def _separate_strands(entries: list[Entry]) -> list[OrderedDict]:
    ans = []
    while entries:
        top, entries = entries[0], entries[1:]
        strand = _populate_strand(top, entries)
        layers: OrderedDict = OrderedDict()
        for e in strand:
            layers.setdefault(e.depth, []).append(e)
        ans.append(layers)
    return ans


def _collect(entries: list[Entry], lengths: list[int]) -> list[list[OrderedDict]]:
    data = []
    entries = sorted(entries, key=lambda e: e.start)
    record_start = 0
    for rec_len in lengths:
        nxt = record_start + rec_len
        local = []
        for e in entries:
            if e.start >= nxt:
                break
            if e.start + e.length <= record_start:
                continue
            local.append(_fill(e, e.start - record_start, rec_len))
        data.append(_separate_strands(local))
        record_start += rec_len
    return data


def _sequences(strands: list[OrderedDict], tbs_type: int = 8) -> list[tuple[int, dict]]:
    ans: list[tuple[int, dict]] = []
    last_index = None
    first_entry = None
    for strand in strands:
        for entries in strand.values():
            for e in entries:
                if first_entry is None:
                    first_entry = e
    for strand in strands:
        seqs: list[tuple[int, dict]] = []
        for entries in strand.values():
            extra: dict = {}
            if entries[-1].action == "spans":
                extra[0b1] = 0
            if entries[0] is first_entry:
                extra[0b10] = tbs_type
            if len(entries) > 1:
                extra[0b100] = len(entries)
            index = entries[0].index - (entries[0].parent or 0)
            if ans and not seqs:
                index = last_index - entries[0].index
                if index < 0:
                    if tbs_type == 5:
                        index = -index
                    else:
                        raise NegativeStrandIndex()
                else:
                    extra[0b1000] = True
            last_index = entries[-1].index
            seqs.append((index, extra))
        for i, seq in enumerate(seqs):
            if i + 1 < len(seqs) and 0b1 in seq[1] and 0b1 in seqs[i + 1][1]:
                del seq[1][0b1]
        ans.extend(seqs)
    return ans


def _to_bytes(sequences: list[tuple[int, dict]]) -> bytes:
    out = []
    flag_size = 3
    for val, extra in sequences:
        out.append(encode_tbs(val, extra, flag_size))
        flag_size = 4
    return b"".join(out)


def calculate_tbs(table: list[dict], lengths: list[int]) -> list[bytes]:
    """Bytes TBS (já com o tamanho no fim) para cada registro de texto."""
    entries = [Entry(r["index"], r["offset"], r["length"], r["depth"], r.get("parent"), r.get("first_child"),
                     r.get("last_child"), r["label"], None, None, None, None) for r in table]
    data = _collect(entries, lengths)
    try:
        raw = [_to_bytes(_sequences(s)) for s in data]
    except NegativeStrandIndex:
        raw = [_to_bytes(_sequences(s, tbs_type=5)) for s in data]
    return [encode_trailing_data(r) for r in raw]
