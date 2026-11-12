"""PDF → texto fluido.

Etapas:

1. Extrai linhas e trechos (fonte, tamanho, negrito, itálico, links) de cada página.
2. Estatísticas do livro: tamanho do corpo do texto (o que cobre mais
   caracteres, regra do Calibre), vocabulário (para a hifenização) e linhas
   repetidas no topo/pé das páginas (cabeçalhos e rodapés).
3. Por página: tabelas, gráficos vetoriais e imagens viram figuras; o texto é
   ordenado (uma ou duas colunas) e agrupado em parágrafos.
4. Parágrafos que continuam na coluna ou página seguinte são juntados.
5. Títulos (por tamanho relativo e padrões de capítulo) dividem o livro em
   arquivos; o sumário vem dos marcadores do PDF ou dos títulos detectados.

Proteção herdada do Calibre: se os títulos detectados derem menos de 2
entradas de sumário e o PDF tiver marcadores, os marcadores ganham.
"""

from __future__ import annotations

import difflib
import html
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from ..core import text as T
from ..core.errors import CancelledError
from ..core.model import Book, Document, PageTarget, Resource
from .base import InputContext, add_stylesheet
from .pdf import TEXT_FLAGS, nest_toc, outline_entries

MAX_DOC_BYTES = 200_000  # abaixo dos 260 KB que o Adobe Digital Editions aceita

BASE_CSS = """@namespace epub "http://www.idpf.org/2007/ops";
body { margin: 0 2%; line-height: 1.4; widows: 2; orphans: 2; }
p { margin: 0; text-indent: 1.4em; }
p.ni, h1 + p, h2 + p, h3 + p, div.img + p, div.fig + p, table + p, p.c + p { text-indent: 0; }
p.c { text-align: center; text-indent: 0; margin: 0.6em 0; }
p.sb { margin: 1.2em 0; }
p.nota { font-size: 0.85em; text-indent: 0; margin-top: 0.3em; }
h1, h2, h3 { text-align: center; line-height: 1.25; margin: 1.6em 0 0.9em; text-indent: 0;
             page-break-after: avoid; }
h1 { font-size: 1.6em; } h2 { font-size: 1.3em; } h3 { font-size: 1.1em; }
h1.ch, h2.ch { page-break-before: always; margin-top: 2.5em; }
div.img, div.fig { text-align: center; margin: 1em 0; page-break-inside: avoid; text-indent: 0; }
div.img img, div.fig img { max-width: 100%; height: auto; }
table.tbl { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; }
table.tbl td, table.tbl th { border: 1px solid #888; padding: 0.2em 0.4em; text-align: left;
                             vertical-align: top; }
table.tbl th { font-weight: bold; }
"""


# ------------------------------------------------------------- estruturas
@dataclass
class Span:
    text: str
    size: float
    bold: bool
    italic: bool
    sup: bool
    link: str | None = None


@dataclass
class Line:
    spans: list[Span]
    x0: float
    y0: float
    x1: float
    y1: float
    page: int

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def size(self) -> float:
        c: Counter = Counter()
        for s in self.spans:
            c[s.size] += len(s.text.strip()) or 1
        return c.most_common(1)[0][0]

    @property
    def bold(self) -> bool:
        chars = [s for s in self.spans if s.text.strip()]
        return bool(chars) and all(s.bold for s in chars)


@dataclass
class Block:
    lines: list[Line]
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Item:
    kind: str  # para | heading | image | table | figure | pb
    page: int
    y0: float = 0
    x0: float = 0
    x1: float = 0
    y1: float = 0
    parts: list = field(default_factory=list)  # Line ou ("pb", página)
    html: str = ""
    level: int = 0
    chapter: bool = False
    css: str = ""
    anchor: str = ""

    def text(self) -> str:
        return " ".join(p.text for p in self.parts if isinstance(p, Line)).strip()

    def lines(self) -> list[Line]:
        return [p for p in self.parts if isinstance(p, Line)]

    def size(self) -> float:
        c: Counter = Counter()
        for ln in self.lines():
            c[ln.size] += len(ln.text)
        return c.most_common(1)[0][0] if c else 0.0


# --------------------------------------------------------------- extração
_BOLD_RE = re.compile(r"bold|black|heavy|semibold|demi", re.I)
_ITALIC_RE = re.compile(r"italic|oblique", re.I)


def _page_links(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    out = []
    for ln in page.get_links():
        r = pymupdf.Rect(ln["from"])
        if ln.get("kind") == pymupdf.LINK_URI and ln.get("uri"):
            out.append((r, ln["uri"]))
        elif ln.get("kind") == pymupdf.LINK_GOTO and ln.get("page", -1) >= 0:
            out.append((r, f"@@pg{ln['page'] + 1}"))
    return out


def extract_blocks(page: pymupdf.Page, pno: int) -> list[Block]:
    links = _page_links(page)
    blocks: list[Block] = []
    d = page.get_text("dict", flags=TEXT_FLAGS)
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines: list[Line] = []
        for ln in b.get("lines", []):
            if abs(ln.get("dir", (1, 0))[0]) < 0.9:
                continue  # texto girado (notas na margem, marcas d'água)
            spans: list[Span] = []
            for s in ln.get("spans", []):
                t = T.clean_text(s.get("text", ""))
                if not t:
                    continue
                font, flags = s.get("font", ""), s.get("flags", 0)
                sb = pymupdf.Rect(s["bbox"])
                link = None
                for r, href in links:
                    inter = sb & r
                    if not inter.is_empty and abs(inter) > 0.5 * max(abs(sb), 1e-6):
                        link = href
                        break
                spans.append(Span(t, round(float(s.get("size", 0)) * 2) / 2,
                                  bool(flags & 16) or bool(_BOLD_RE.search(font)),
                                  bool(flags & 2) or bool(_ITALIC_RE.search(font)),
                                  bool(flags & 1), link))
            if spans and "".join(s.text for s in spans).strip():
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(Line(spans, x0, y0, x1, y1, pno))
        if lines:
            blocks.append(Block(lines, min(l.x0 for l in lines), min(l.y0 for l in lines),
                                max(l.x1 for l in lines), max(l.y1 for l in lines)))
    return blocks


# ---------------------------------------------------- cabeçalho/rodapé
def find_running_lines(pages: dict[int, list[Block]], heights: dict[int, float]) -> set[tuple[int, int, int]]:
    """Linhas a remover: (página, bloco, linha)."""
    counts: Counter = Counter()
    candidates: dict[tuple[int, int, int], str] = {}
    for pno, blocks in pages.items():
        h = heights[pno]
        seen: set[str] = set()
        for bi, b in enumerate(blocks):
            for li, ln in enumerate(b.lines):
                if ln.y1 <= h * 0.09 or ln.y0 >= h * 0.91:
                    key = T.repeat_key(ln.text)
                    candidates[(pno, bi, li)] = key
                    if key and key not in seen:
                        counts[key] += 1
                        seen.add(key)
    n = len(pages)
    threshold = 2 if n < 6 else max(3, int(0.2 * n))
    remove = set()
    for loc, key in candidates.items():
        text = key.replace("#", "1")
        if counts[key] >= threshold or T.is_page_number(text) or not key.strip("# "):
            remove.add(loc)
    return remove


# -------------------------------------------------------------- ordenação
def _column_of(x0: float, x1: float, mid: float, w: float) -> int:
    if x1 < mid + 0.03 * w:
        return 0
    if x0 > mid - 0.03 * w:
        return 1
    return -1


def order_regions(regions: list, page_rect: pymupdf.Rect, multicolumn: bool) -> list:
    if not multicolumn:
        return sorted(regions, key=lambda r: (round(r.y0 / 3), r.x0))
    w = page_rect.width
    mid = page_rect.x0 + w / 2
    spanning = sorted([r for r in regions if _column_of(r.x0, r.x1, mid, w) == -1], key=lambda r: r.y0)
    bounds = [float("-inf")] + [s.y0 for s in spanning] + [float("inf")]
    out: list = []
    for k in range(len(bounds) - 1):
        band = [r for r in regions if _column_of(r.x0, r.x1, mid, w) != -1 and bounds[k] <= r.y0 < bounds[k + 1]]
        out += sorted([r for r in band if _column_of(r.x0, r.x1, mid, w) == 0], key=lambda r: r.y0)
        out += sorted([r for r in band if _column_of(r.x0, r.x1, mid, w) == 1], key=lambda r: r.y0)
        if k < len(spanning):
            out.append(spanning[k])
    return out


def is_multicolumn(blocks: list[Block], page_rect: pymupdf.Rect) -> bool:
    from .pdf import multicolumn

    return multicolumn([(b.x0, b.y0, b.x1, b.y1, sum(len(l.text) for l in b.lines)) for b in blocks], page_rect)


# ------------------------------------------------------------- parágrafos
def split_paragraphs(block: Block, body: float) -> list[list[Line]]:
    lines = block.lines
    gaps = sorted(b.y0 - a.y1 for a, b in zip(lines, lines[1:]) if b.y0 > a.y1 - 1)
    gap_med = gaps[len(gaps) // 2] if gaps else body * 0.3
    paras: list[list[Line]] = []
    cur: list[Line] = []
    for ln in lines:
        if cur:
            prev = cur[-1]
            same_row = abs(ln.y0 - prev.y0) < 0.5 * body
            new = False
            if not same_row:
                if abs(ln.size - prev.size) > 0.15 * body:
                    new = True
                elif ln.y0 - prev.y1 > max(gap_med * 1.8, body * 0.9):
                    new = True
                elif ln.x0 - block.x0 > body * 0.9 and T.ends_sentence(prev.text):
                    new = True
                elif prev.x1 < block.x1 - body * 2.5 and T.ends_sentence(prev.text):
                    new = True
            if new:
                paras.append(cur)
                cur = []
        cur.append(ln)
    if cur:
        paras.append(cur)
    return paras


# ------------------------------------------------------------ figuras
def _vector_regions(page: pymupdf.Page, exclude: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    pr = page.rect
    try:
        paths = page.get_drawings()
    except Exception:  # noqa: BLE001
        return []
    rects = []
    for p in paths:
        r = pymupdf.Rect(p.get("rect")) & pr
        if r.is_empty and not (r.width or r.height):
            continue
        if abs(r) > 0.85 * abs(pr):
            continue  # fundo da página
        w, h = r.width, r.height
        if max(w, h) < 12:
            continue
        if min(w, h) < 2 and max(w, h) > 0.35 * pr.width:
            continue  # fios e sublinhados
        if any(r in t for t in exclude):
            continue
        rects.append(r + (-6, -6, 6, 6))
    clusters: list[list] = []
    for r in rects:
        merged = [c for c in clusters if c[0].intersects(r)]
        if merged:
            base = merged[0]
            base[0] |= r
            base[1] += 1
            for other in merged[1:]:
                base[0] |= other[0]
                base[1] += other[1]
                clusters.remove(other)
        else:
            clusters.append([pymupdf.Rect(r), 1])
    return [(c[0] & pr) for c in clusters if c[1] >= 4 and abs(c[0]) > 0.015 * abs(pr)]


def _table_html(table, words: list) -> str | None:
    """Monta a tabela pelas palavras dentro de cada célula: a extração de
    células do PyMuPDF pode separar pontuação ("3.400" → "3400 .")."""
    rows_out: list[list[str]] = []
    try:
        rows = table.rows
        for row in rows:
            cells = []
            for cell in row.cells:
                if cell is None:
                    cells.append("")
                    continue
                cr = pymupdf.Rect(cell)
                ws = [w for w in words if _inside(w[0], w[1], w[2], w[3], cr)]
                ws.sort(key=lambda w: (round(w[3] / 3), w[0]))
                cells.append(T.clean_text(" ".join(w[4] for w in ws)))
            rows_out.append(cells)
    except Exception:  # noqa: BLE001 - versão do PyMuPDF sem .rows: usa extract()
        try:
            rows_out = [[T.clean_text((c or "").replace("\n", " ").strip()) for c in r]
                        for r in table.extract() if r]
        except Exception:  # noqa: BLE001
            return None
    if len(rows_out) < 2 or max(len(r) for r in rows_out) < 2:
        return None
    out = ['<table class="tbl">']
    for i, r in enumerate(rows_out):
        tag = "th" if i == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


# ------------------------------------------------------------- renderização
def _style_key(s: Span) -> tuple:
    return (s.bold, s.italic, s.sup, s.link)


def render_inline(item: Item, plain: set[str], hyph: set[str], dehyphenate: bool, heading: bool = False) -> str:
    runs: list[list] = []  # [texto, chave]

    def push(text: str, key: tuple) -> None:
        if runs and runs[-1][1] == key:
            runs[-1][0] += text
        else:
            runs.append([text, key])

    markers: list[tuple[int, str]] = []
    prev_line: Line | None = None
    for part in item.parts:
        if not isinstance(part, Line):
            markers.append((len(runs), part[1]))
            runs.append(["", ("pb", part[1])])
            continue
        spans = [Span(s.text, s.size, s.bold, s.italic, s.sup, s.link) for s in part.spans]
        if prev_line is not None:
            last = next((r for r in reversed(runs) if r[1][0] != "pb"), None)
            if last is not None:
                tail = last[0].rstrip()
                first = spans[0].text.lstrip()
                if tail.endswith("­"):
                    last[0] = tail[:-1]
                    spans[0].text = first
                elif dehyphenate and tail.endswith("-") and len(tail) > 1 and tail[-2].isalpha():
                    joined, _sep = T.join_hyphenated(tail, first, plain, hyph)
                    head_len = len(tail) - 1
                    last[0] = joined[:head_len] if _sep == "" else tail
                    spans[0].text = first if _sep == "-" else joined[head_len:]
                    if _sep == "-" and first.startswith("-"):
                        spans[0].text = first[1:]
                else:
                    last[0] = tail + " "
                    spans[0].text = first
        for s in spans:
            push(s.text, _style_key(s))
        prev_line = part
    out = []
    for text, key in runs:
        if key[0] == "pb":
            out.append(f'<span epub:type="pagebreak" role="doc-pagebreak" id="pg{key[1]}" title="{key[1]}"></span>')
            continue
        t = html.escape(re.sub(r"[ \t]+", " ", text))
        if not t:
            continue
        bold, italic, sup, link = key
        if sup:
            t = f"<sup>{t}</sup>"
        if italic:
            t = f"<em>{t}</em>"
        if bold and not heading:
            t = f"<strong>{t}</strong>"
        if link:
            t = f'<a href="{html.escape(link, quote=True)}">{t}</a>'
        out.append(t)
    return "".join(out).strip()


# ------------------------------------------------------------- principal
def _inside(x0: float, y0: float, x1: float, y1: float, region: pymupdf.Rect) -> bool:
    """O centro da caixa está dentro da região? (mais robusto que contenção
    total: a caixa detectada de uma tabela costuma ser um pouco menor que o texto)."""
    return region.contains(pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2))


@dataclass
class BookStats:
    body: float
    plain: set
    hyph: set
    remove: set
    decor: set


def collect_stats(doc, ctx: InputContext, book: Book, indices: list[int]) -> tuple[dict, BookStats]:
    """Passo 1: extrai as linhas de todas as páginas e calcula as estatísticas do livro."""
    pages: dict[int, list[Block]] = {}
    heights: dict[int, float] = {}
    xref_count: Counter = Counter()
    for k, i in enumerate(indices):
        if ctx.cancelled():
            raise CancelledError("Conversão cancelada.")
        page = doc[i]
        pages[i] = extract_blocks(page, i)
        heights[i] = page.rect.height
        for x in {im[0] for im in page.get_images(full=False)}:
            xref_count[x] += 1
        if k % 10 == 0:
            ctx.progress(0.05 + 0.35 * k / len(indices), "Lendo o texto")
    remove = find_running_lines(pages, heights) if ctx.opts.remove_headers_footers else set()
    size_chars: Counter = Counter()
    all_text = []
    for pno, blocks in pages.items():
        for bi, b in enumerate(blocks):
            for li, ln in enumerate(b.lines):
                if (pno, bi, li) in remove:
                    continue
                for s in ln.spans:
                    size_chars[s.size] += len(s.text.strip())
                all_text.append(ln.text)
    body = size_chars.most_common(1)[0][0] if size_chars else 11.0
    plain, hyph = T.vocabulary(" ".join(all_text))
    decor = {x for x, c in xref_count.items() if c >= 3 and c > 0.25 * len(indices)}
    if sum(size_chars.values()) < 40 * max(1, len(indices)):
        book.warn("Este PDF tem pouco texto extraível; o layout fiel deve dar um resultado melhor.")
    return pages, BookStats(body, plain, hyph, remove, decor)


class PageStructure:
    """Passo 2: transforma uma página em regiões (tabela, figura, imagem,
    parágrafo) na ordem de leitura."""

    def __init__(self, doc, ctx: InputContext, book: Book, stats: BookStats, extra_re):
        self.doc, self.ctx, self.book, self.stats, self.extra_re = doc, ctx, book, stats, extra_re
        self.opts = ctx.opts
        self.img_dir = ctx.ws.sub("images")
        self.image_cache: dict[int, str] = {}
        self.fig_n = 0

    def regions(self, i: int, blocks: list[Block]) -> list[Item]:
        page = self.doc[i]
        excluded: list[pymupdf.Rect] = []
        regions = self._tables(page, excluded)
        regions += self._figures(page, blocks, excluded)
        regions += self._images(page, blocks, excluded)
        regions += self._text(i, blocks, excluded)
        ordered = order_regions(regions, page.rect, is_multicolumn(blocks, page.rect))
        for it in ordered:
            if it.kind == "para":
                _classify_para(it, self.stats.body, page.rect, self.extra_re, self.opts.detect_chapters)
        return ordered

    def _tables(self, page, excluded: list) -> list[Item]:
        if self.opts.tables == "text":
            return []
        try:  # conta segmentos, não caminhos: uma tabela inteira pode ser um caminho só
            if sum(len(d.get("items", ())) for d in page.get_cdrawings()) < 4:
                return []
            found = page.find_tables().tables
        except Exception:  # noqa: BLE001
            return []
        out = []
        words = None
        for t in found:
            if t.row_count < 2 or t.col_count < 2:
                continue
            tr = pymupdf.Rect(t.bbox)
            markup = None
            if self.opts.tables == "html":
                if words is None:
                    words = page.get_text("words")
                markup = _table_html(t, words)
            if markup is None:
                self.fig_n += 1
                markup = _render_clip(page, tr, self.img_dir, f"tab{self.fig_n:04d}", self.book, self.ctx.grayscale)
            out.append(Item("table", page.number, tr.y0, tr.x0, tr.x1, tr.y1, html=markup))
            excluded.append(tr + (-4, -4, 4, 4))
        return out

    def _figures(self, page, blocks: list[Block], excluded: list) -> list[Item]:
        if not self.opts.vector_graphics:
            return []
        out = []
        for vr in _vector_regions(page, excluded):
            inside = sum(len(l.text) for b in blocks for l in b.lines if _inside(l.x0, l.y0, l.x1, l.y1, vr))
            if inside > 400:
                continue  # é uma caixa de texto, não um gráfico
            self.fig_n += 1
            markup = _render_clip(page, vr, self.img_dir, f"fig{self.fig_n:04d}", self.book,
                                  self.ctx.grayscale, css="fig")
            out.append(Item("figure", page.number, vr.y0, vr.x0, vr.x1, vr.y1, html=markup))
            excluded.append(vr)
        return out

    def _images(self, page, blocks: list[Block], excluded: list) -> list[Item]:
        pr = page.rect
        page_chars = sum(len(l.text) for b in blocks for l in b.lines)
        out = []
        for info in page.get_image_info(xrefs=True):
            ir = pymupdf.Rect(info["bbox"]) & pr
            xref = info.get("xref", 0)
            if ir.is_empty or any(_inside(*ir, e) for e in excluded) or xref in self.stats.decor:
                continue
            if abs(ir) < 0.015 * abs(pr) and min(info.get("width", 0), info.get("height", 0)) < 64:
                continue  # ícones e vinhetas
            if abs(ir) > 0.85 * abs(pr) and page_chars > 300:
                continue  # imagem de fundo com texto por cima
            href = self.image_cache.get(xref) if xref else None
            if href is None:
                href = _extract_image(self.doc, page, info, ir, self.img_dir, self.book)
                if not href:
                    continue
                if xref:
                    self.image_cache[xref] = href
            out.append(Item("image", page.number, ir.y0, ir.x0, ir.x1, ir.y1,
                            html=f'<div class="img"><img src="../{href}" alt=""/></div>'))
        return out

    def _text(self, i: int, blocks: list[Block], excluded: list) -> list[Item]:
        out = []
        for bi, b in enumerate(blocks):
            kept = [ln for li, ln in enumerate(b.lines) if (i, bi, li) not in self.stats.remove
                    and not any(_inside(ln.x0, ln.y0, ln.x1, ln.y1, e) for e in excluded)]
            if not kept:
                continue
            nb = Block(kept, min(l.x0 for l in kept), min(l.y0 for l in kept),
                       max(l.x1 for l in kept), max(l.y1 for l in kept))
            for para in split_paragraphs(nb, self.stats.body):
                out.append(Item("para", i, para[0].y0, min(l.x0 for l in para),
                                max(l.x1 for l in para), para[-1].y1, parts=list(para)))
        return out


def build_reflow(doc: pymupdf.Document, ctx: InputContext, book: Book, indices: list[int]) -> None:
    opts = ctx.opts
    extra_re = None
    if opts.chapter_pattern:
        try:
            extra_re = re.compile(opts.chapter_pattern, re.I)
        except re.error:
            book.warn("O padrão extra de capítulo é uma expressão regular inválida e foi ignorado.")

    pages, stats = collect_stats(doc, ctx, book, indices)
    body, plain, hyph = stats.body, stats.plain, stats.hyph
    structure = PageStructure(doc, ctx, book, stats, extra_re)
    cover_idx = opts.cover_page - 1
    items: list[Item] = []
    for k, i in enumerate(indices):
        if ctx.cancelled():
            raise CancelledError("Conversão cancelada.")
        items.append(Item("pb", i, y0=-1))
        page_chars = sum(len(l.text) for b in pages[i] for l in b.lines)
        if i == cover_idx and page_chars < 200:
            continue  # a capa já entra como imagem de capa
        items.extend(structure.regions(i, pages[i]))
        if k % 10 == 0:
            ctx.progress(0.4 + 0.4 * k / len(indices), "Montando parágrafos")

    items = _merge_continuations(items)
    _assign_heading_levels(items, body)

    # ---- passo 3: documentos
    outline = [e for e in outline_entries(doc) if e[2] in pages]
    split_at: set[int] = set()
    toc_targets: dict[int, int] = {}  # índice no outline → índice do item
    if len(outline) >= 2:
        for n_entry, (level, title, pno) in enumerate(outline):
            idx = _match_heading(items, pno, title)
            if idx is not None:
                toc_targets[n_entry] = idx
                if level == 1:
                    split_at.add(idx)
            elif level == 1:
                pb = next((j for j, it in enumerate(items) if it.kind == "pb" and it.page == pno), None)
                if pb is not None:
                    split_at.add(pb)
    else:
        chapters = [j for j, it in enumerate(items) if it.kind == "heading" and it.chapter]
        if not chapters:
            h1 = [j for j, it in enumerate(items) if it.kind == "heading" and it.level == 1]
            if 2 <= len(h1) <= max(2, len(indices) // 2):
                chapters = h1
        split_at.update(chapters)
    for j in split_at:
        if items[j].kind == "heading":
            items[j].css = "ch"

    docs = _emit_documents(items, split_at, plain, hyph, opts.dehyphenate)
    page_doc: dict[int, str] = {}
    anchor_doc: dict[str, str] = {}
    for href, _title, body_html in docs:
        for m in re.finditer(r'id="(pg(\d+)|h\d+)"', body_html):
            anchor_doc.setdefault(m.group(1), href)
            if m.group(2):
                page_doc.setdefault(int(m.group(2)), href)

    def fix_link(m: re.Match, doc_href: str) -> str:
        target = m.group(1)
        dest = anchor_doc.get(f"pg{target}")
        if not dest:
            return 'href="#"'
        rel = dest.rsplit("/", 1)[-1]
        return f'href="{rel}#pg{target}"' if dest != doc_href else f'href="#pg{target}"'

    add_stylesheet(book, ctx.ws, "book.css", _book_css(opts))
    for n, (href, title, body_html) in enumerate(docs):
        body_html = re.sub(r'href="@@pg(\d+)"', lambda m, h=href: fix_link(m, h), body_html)
        book.documents.append(Document(href=href, title=title, body=body_html, id=f"c{n + 1:03d}",
                                       lang=book.metadata.language))
    for i in indices:
        n = i + 1
        if n in page_doc:
            try:
                label = doc[i].get_label() or str(n)
            except Exception:  # noqa: BLE001
                label = str(n)
            book.page_list.append(PageTarget(label, f"{page_doc[n]}#pg{n}"))
    book.layout = "reflow"

    # ---- sumário
    flat: list[tuple[int, str, str]] = []
    for n_entry, (level, title, pno) in enumerate(outline):
        j = toc_targets.get(n_entry)
        if j is not None and items[j].anchor and items[j].anchor in anchor_doc:
            flat.append((level, title, f"{anchor_doc[items[j].anchor]}#{items[j].anchor}"))
        elif (pno + 1) in page_doc:
            flat.append((level, title, f"{page_doc[pno + 1]}#pg{pno + 1}"))
    detected = [(it.level, it.text()[:100], f"{anchor_doc[it.anchor]}#{it.anchor}")
                for it in items if it.kind == "heading" and it.level <= 2 and it.anchor in anchor_doc]
    if len(flat) >= 2:
        book.toc = nest_toc(flat)
    elif len(detected) >= 2:
        book.toc = nest_toc(detected)
    elif book.documents:
        book.toc = nest_toc([(1, "Início", book.documents[0].href)])
    ctx.progress(0.95, "Texto pronto")


def _book_css(opts) -> str:
    css = BASE_CSS
    css += "body { text-align: justify; }\n" if opts.text_align == "justify" else "body { text-align: left; }\n"
    if opts.paragraph_style == "spaced":
        css += "p { text-indent: 0; margin: 0 0 0.75em; }\n"
    return css


def _render_clip(page, rect, img_dir, name, book: Book, gray: bool, css: str = "img") -> str:
    clip = (rect + (-3, -3, 3, 3)) & page.rect
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), clip=clip, alpha=False,
                          colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
    path = img_dir / f"{name}.png"
    path.write_bytes(pix.tobytes("png"))
    href = f"images/{name}.png"
    book.add_resource(Resource(href, "image/png", path))
    return f'<div class="{css}"><img src="../{href}" alt=""/></div>'


def _extract_image(doc, page, info, rect, img_dir, book: Book) -> str | None:
    """Extrai a imagem original (sem recomprimir) sempre que possível."""
    xref = info.get("xref", 0)
    transform = info.get("transform") or (1, 0, 0, 1, 0, 0)
    rotated = abs(transform[1]) > 1e-3 or abs(transform[2]) > 1e-3
    name = f"img{xref:05d}" if xref else f"inl{page.number:04d}_{int(rect.y0)}"
    try:
        if not xref or rotated:
            raise ValueError("renderizar")
        data = doc.extract_image(xref)
        ext = data.get("ext", "")
        cmyk = data.get("colorspace", 3) == 4
        if data.get("smask") or ext not in ("jpeg", "jpg", "png", "gif") or cmyk:
            pix = pymupdf.Pixmap(doc, xref)
            if data.get("smask"):
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, data["smask"]))
            if pix.colorspace and pix.colorspace.n >= 4:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            raw, ext = pix.tobytes("png"), "png"
        else:
            raw = data["image"]
            ext = "jpg" if ext == "jpeg" else ext
        if len(raw) > 6_000_000:
            from ..core.imaging import to_jpeg_bytes

            raw, ext = to_jpeg_bytes(raw, 88, max_side=3000), "jpg"
    except Exception:  # noqa: BLE001 - imagem embutida/girada: renderiza a área
        clip = rect & page.rect
        if clip.is_empty:
            return None
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        raw, ext = pix.tobytes("jpeg", jpg_quality=88), "jpg"
    media = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}[ext]
    path = img_dir / f"{name}.{ext}"
    path.write_bytes(raw)
    href = f"images/{name}.{ext}"
    book.add_resource(Resource(href, media, path))
    return href


_SCENE_BREAK = re.compile(r"^\s*([*#~•·◆◇§]\s*){1,5}$|^\s*[—–-]{1,3}\s*$")


def _classify_para(it: Item, body: float, pr: pymupdf.Rect, extra_re, detect_chapters: bool) -> None:
    text = it.text()
    size = it.size()
    lines = it.lines()
    first = lines[0].text.strip() if lines else ""
    n = len(text)
    if _SCENE_BREAK.match(text):
        it.css = "c sb"
        return
    chapter = detect_chapters and n <= 150 and (T.is_chapter_title(first, extra_re))
    numeral = bool(T.NUMERAL_ONLY_RE.match(text)) and size >= body * 1.15
    big = size >= body * 1.18 and n <= 250
    boldline = all(l.bold for l in lines) and n <= 90 and not T.ends_sentence(text) and len(lines) <= 2
    if chapter or numeral or big or boldline:
        it.kind = "heading"
        it.chapter = chapter or numeral
        it.size_hint = size if (big or chapter or numeral) else body  # type: ignore[attr-defined]
        return
    if size < body * 0.86 and it.y0 > pr.height * 0.75:
        it.css = "nota"
        return
    width = pr.width
    left_gap, right_gap = it.x0 - pr.x0, pr.x1 - it.x1
    if len(lines) <= 3 and abs(left_gap - right_gap) < 0.06 * width and (it.x1 - it.x0) < 0.6 * width:
        it.css = "c"


def _merge_continuations(items: list[Item]) -> list[Item]:
    out: list[Item] = []
    pending_pb: list[Item] = []
    for it in items:
        if it.kind == "pb":
            pending_pb.append(it)
            continue
        prev = out[-1] if out else None
        if (prev is not None and prev.kind == "para" and it.kind == "para" and not it.css
                and not prev.css and _continues(prev, it)):
            for pb in pending_pb:
                prev.parts.append(("pb", pb.page + 1))
            prev.parts.extend(it.parts)
            pending_pb = []
            continue
        out.extend(pending_pb)
        pending_pb = []
        out.append(it)
    out.extend(pending_pb)
    return out


def _continues(prev: Item, nxt: Item) -> bool:
    a, b = prev.text(), nxt.text()
    if not a or not b:
        return False
    if a.endswith("-") and a[-2:-1].isalpha() and T.starts_lowercase(b):
        return True
    return not T.ends_sentence(a) and T.starts_lowercase(b)


def _assign_heading_levels(items: list[Item], body: float) -> None:
    sizes = sorted({round(getattr(it, "size_hint", body)) for it in items if it.kind == "heading"
                    and getattr(it, "size_hint", body) > body * 1.1}, reverse=True)
    level_of = {s: min(3, k + 1) for k, s in enumerate(sizes)}
    n = 0
    for it in items:
        if it.kind != "heading":
            continue
        hint = round(getattr(it, "size_hint", body))
        it.level = level_of.get(hint, 3 if sizes else 2)
        if it.chapter:
            it.level = min(it.level, 2)
        n += 1
        it.anchor = f"h{n}"


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def _match_heading(items: list[Item], pno: int, title: str) -> int | None:
    want = _norm(title)
    best, best_score = None, 0.0
    for j, it in enumerate(items):
        if it.page != pno or it.kind not in ("heading", "para"):
            continue
        if it.kind == "para" and len(it.text()) > 150:
            continue  # parágrafo longo que só começa com o título: não vira título
        got = _norm(it.text())[: len(want) + 20]
        if not got:
            continue
        score = difflib.SequenceMatcher(None, want, got).ratio()
        if got.startswith(want) or want.startswith(got):
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = j, score
    if best is not None and best_score >= 0.6:
        it = items[best]
        if it.kind == "para":
            it.kind, it.level = "heading", 2
            it.anchor = it.anchor or f"t{best}"
        return best
    return None


def _emit_documents(items: list[Item], split_at: set[int], plain, hyph, dehyphenate: bool):
    docs: list[tuple[str, str, str]] = []
    chunks: list[str] = []
    size = 0
    title = ""
    part = 0

    def flush() -> None:
        nonlocal chunks, size, title, part
        if any(c.strip() for c in chunks):
            part += 1
            docs.append((f"text/c{part:03d}.xhtml", title or f"Parte {part}", "\n".join(chunks)))
        chunks, size = [], 0

    for j, it in enumerate(items):
        if j in split_at and any(not c.startswith('<div class="pb"') for c in chunks):
            # os marcadores de página logo antes do título pertencem ao novo capítulo
            carry: list[str] = []
            while chunks and chunks[-1].startswith('<div class="pb"'):
                carry.insert(0, chunks.pop())
            flush()
            chunks, size, title = carry, sum(len(c) for c in carry), ""
        if it.kind == "pb":
            markup = (f'<div class="pb" epub:type="pagebreak" role="doc-pagebreak" '
                      f'id="pg{it.page + 1}" title="{it.page + 1}"></div>')
        elif it.kind == "heading":
            text = render_inline(it, plain, hyph, dehyphenate, heading=True)
            cls = f' class="{it.css}"' if it.css else ""
            markup = f'<h{it.level} id="{it.anchor}"{cls}>{text}</h{it.level}>'
            if j in split_at or not title:
                title = it.text()[:100]
        elif it.kind == "para":
            text = render_inline(it, plain, hyph, dehyphenate)
            cls = f' class="{it.css}"' if it.css else ""
            markup = f"<p{cls}>{text}</p>" if text else ""
        else:
            markup = it.html
        if not markup:
            continue
        if size + len(markup) > MAX_DOC_BYTES and size > 0 and it.kind != "pb":
            flush()
        chunks.append(markup)
        size += len(markup.encode("utf-8"))
    flush()
    return docs
