"""Leitor de PDF.

Dois caminhos, escolhidos pela opção ``mode`` (ou pela análise automática):

* **Layout fiel**: cada página é renderizada na resolução da tela de destino.
  Nada se perde: colunas, tabelas, fórmulas, gráficos vetoriais e fontes sem
  Unicode ficam exatamente como no original. É o que o Calibre não faz: o
  manual dele avisa que PDF como entrada não suporta colunas, tabelas, imagens
  vetoriais, links nem sumário.
* **Texto fluido**: ver :mod:`prelo.inputs.pdf_reflow`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from ..core import text as T
from ..core.errors import ConversionError, DrmError, InvalidOptionError, PasswordRequiredError
from ..core.imaging import NEUTRAL_K, Adjustments
from ..core.model import Book, Metadata, Resource, TocEntry
from ..core.safety import sanitize_filename
from .base import InputContext, InputPlugin, populate_fixed_book
from .render import RenderConfig, render_all

TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP


# ------------------------------------------------------------------ abertura
def _encryption_filter(doc: pymupdf.Document) -> str | None:
    try:
        kind, val = doc.xref_get_key(-1, "Encrypt")
        if kind != "xref":
            return None
        xref = int(val.split()[0])
        kind, filt = doc.xref_get_key(xref, "Filter")
        return filt if kind == "name" else None
    except Exception:  # noqa: BLE001
        return None


def open_pdf(path: Path, password: str = "") -> pymupdf.Document:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ConversionError("Não foi possível abrir o PDF. O arquivo pode estar corrompido.") from exc
    filt = _encryption_filter(doc)
    if filt and filt != "/Standard":
        raise DrmError("Este PDF tem DRM (proteção de editora) e não pode ser convertido.",
                       hint="Use uma cópia do PDF sem proteção.")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise PasswordRequiredError("Este PDF pede uma senha para abrir.",
                                        hint="Informe a senha na opção 'Senha do PDF'.")
    if doc.page_count == 0:
        raise ConversionError("O PDF não tem páginas.")
    return doc


def parse_page_range(spec: str, count: int) -> list[int]:
    """'1-3,7,10-' → índices 0-based."""
    if not spec or not spec.strip():
        return list(range(count))
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m:
            raise InvalidOptionError(f"Intervalo de páginas inválido: '{part}'.", hint="Exemplo: 1-20,25")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1) or 1)
            b = int(m.group(2) or count)
        a, b = max(1, a), min(count, b)
        pages.extend(range(a - 1, b))
    seen: set[int] = set()
    result = [p for p in pages if not (p in seen or seen.add(p))]
    if not result:
        raise InvalidOptionError("O intervalo de páginas não contém nenhuma página do arquivo.")
    return result


# ---------------------------------------------------------------- metadados
_JUNK_TITLE = re.compile(r"(^untitled|^sem t[íi]tulo|\.(docx?|odt|indd|pages|qxp|tex|rtf)$|"
                         r"^microsoft word|^document\d*$|^livro\d*$|^\s*$)", re.I)

STOPWORDS = {
    "pt": "não uma com os ao à é você também muito isso ele ela pelo pela mas foi são está então "
          "porque já só há até depois sem ser ter seu sua nós eu dos das num numa".split(),
    "es": "el y los del las por una con es pero muy también porque está esto eso él yo ya hay "
          "sí más cuando donde todo nada".split(),
    "en": "the and of to is that it was for with his they this have from had but not what were "
          "when there would which their".split(),
    "fr": "le les des et est une dans pour pas qui sur avec il elle ce sont mais nous vous".split(),
    "de": "der die und das ist nicht ein eine mit sich auf für auch dem den von zu im".split(),
    "it": "il che di è non per sono gli della anche ma come più questo nel alla".split(),
}


def detect_language(sample: str) -> str | None:
    words = re.findall(r"[^\W\d_]+", sample.lower())
    if len(words) < 30:
        return None
    counts = Counter(words)
    scores = {lang: sum(counts[w] for w in sw) for lang, sw in STOPWORDS.items()}
    lang, best = max(scores.items(), key=lambda kv: kv[1])
    return lang if best >= 5 else None


def read_metadata(doc: pymupdf.Document, original_name: str) -> Metadata:
    raw = doc.metadata or {}
    md = Metadata()
    title = T.clean_text((raw.get("title") or "").strip())
    if _JUNK_TITLE.search(title):
        title = ""
    if not title:
        stem = Path(original_name).stem
        title = re.sub(r"[_]+", " ", stem).strip() or "Livro"
    md.title = title
    author = T.clean_text((raw.get("author") or "").strip())
    if author and not re.search(r"(^user$|^admin|^owner$|microsoft|adobe)", author, re.I):
        md.authors = [a.strip() for a in re.split(r";|\s+&\s+|\s+e\s+|\s+and\s+", author) if a.strip()]
    md.description = T.clean_text((raw.get("subject") or "").strip())
    keywords = raw.get("keywords") or ""
    md.subjects = [k.strip() for k in re.split(r"[;,]", keywords) if k.strip()][:12]
    date = raw.get("creationDate") or ""
    m = re.search(r"(\d{4})(\d{2})?(\d{2})?", date)
    if m:
        md.date = "-".join(g for g in m.groups() if g)
    lang = None
    try:
        kind, val = doc.xref_get_key(doc.pdf_catalog(), "Lang")
        if kind == "string" and val:
            lang = val.strip()
    except Exception:  # noqa: BLE001
        pass
    if not lang:
        sample = "".join(doc[i].get_text("text") for i in range(min(doc.page_count, 12)))
        lang = detect_language(sample)
    md.language = (lang or "pt").replace("_", "-")
    return md


# ------------------------------------------------------------------ análise
@dataclass
class PdfAnalysis:
    pages: int
    sampled: int
    avg_chars: float
    scanned_ratio: float
    image_heavy_ratio: float
    multicolumn_ratio: float
    vector_heavy_ratio: float
    garbled: float
    has_outline: bool
    recommended: str = "fixed"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def multicolumn(boxes: list[tuple[float, float, float, float, int]], rect: pymupdf.Rect) -> bool:
    """Há colunas quando blocos da metade esquerda e da direita ficam lado a
    lado (sobrepostos na vertical) e cada lado tem uma parte relevante do texto.

    ``boxes``: (x0, y0, x1, y1, caracteres) de cada bloco de texto."""
    w = rect.width
    mid = rect.x0 + w / 2
    boxes = [b for b in boxes if b[4] >= 40]
    total = sum(b[4] for b in boxes)
    left = [b for b in boxes if b[2] < mid + 0.03 * w]
    right = [b for b in boxes if b[0] > mid - 0.03 * w]
    if total < 300 or sum(b[4] for b in left) < 0.2 * total or sum(b[4] for b in right) < 0.2 * total:
        return False
    for a in left:
        for b in right:
            overlap = min(a[3], b[3]) - max(a[1], b[1])
            if overlap > 0.5 * min(a[3] - a[1], b[3] - b[1]):
                return True
    return False


def _is_multicolumn(page: pymupdf.Page) -> bool:
    boxes = [(x0, y0, x1, y1, len(t.strip())) for x0, y0, x1, y1, t, _bn, bt in page.get_text("blocks") if bt == 0]
    return multicolumn(boxes, page.rect)


def analyze(doc: pymupdf.Document, sample: int = 40) -> PdfAnalysis:
    n = doc.page_count
    step = max(1, n // sample)
    idx = list(range(0, n, step))[:sample]
    chars = scanned = image_heavy = multicol = vector_heavy = 0
    garbled_total = text_total = 0
    for i in idx:
        page = doc[i]
        area = abs(page.rect) or 1
        txt = page.get_text("text")
        c = len(txt.strip())
        chars += c
        text_total += c
        garbled_total += T.garbled_ratio(txt) * c
        img_area = 0.0
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            img_area += abs(r)
        coverage = min(1.0, img_area / area)
        if coverage > 0.6 and c < 80:
            scanned += 1
        elif coverage > 0.35:
            image_heavy += 1
        if c > 300 and _is_multicolumn(page):
            multicol += 1
        try:
            if len(page.get_cdrawings()) > 250:
                vector_heavy += 1
        except Exception:  # noqa: BLE001
            pass
    k = len(idx) or 1
    a = PdfAnalysis(
        pages=n, sampled=len(idx), avg_chars=chars / k, scanned_ratio=scanned / k,
        image_heavy_ratio=image_heavy / k, multicolumn_ratio=multicol / k,
        vector_heavy_ratio=vector_heavy / k,
        garbled=(garbled_total / text_total) if text_total else 0.0,
        has_outline=bool(doc.get_toc()),
    )
    reasons: list[str] = []
    if a.scanned_ratio >= 0.3:
        reasons.append(f"{a.scanned_ratio:.0%} das páginas são digitalizadas (imagem sem texto).")
    if a.garbled > 0.05:
        reasons.append("O texto do PDF usa fontes sem mapeamento de caracteres: extraído, viraria lixo.")
    if a.image_heavy_ratio >= 0.4:
        reasons.append(f"{a.image_heavy_ratio:.0%} das páginas são dominadas por imagens.")
    if a.multicolumn_ratio >= 0.25:
        reasons.append(f"{a.multicolumn_ratio:.0%} das páginas têm várias colunas.")
    if a.vector_heavy_ratio >= 0.2:
        reasons.append("Muitas páginas têm gráficos ou diagramas vetoriais.")
    if not reasons and a.avg_chars < 250:
        reasons.append("Pouco texto por página (quadrinhos, livro ilustrado ou apresentação).")
    if reasons:
        a.recommended, a.reasons = "fixed", reasons
    else:
        a.recommended = "reflow"
        a.reasons = ["Livro de texto corrido em uma coluna: o texto se adapta bem a telas pequenas."]
    return a


# ------------------------------------------------------------- recortes
def _content_rect(page: pymupdf.Page) -> pymupdf.Rect | None:
    """Área com conteúdo, calculada pelos objetos do PDF (preciso e rápido)."""
    pr = page.rect
    r = pymupdf.Rect()
    for x0, y0, x1, y1, txt, _bn, _bt in page.get_text("blocks"):
        if txt.strip():
            r |= pymupdf.Rect(x0, y0, x1, y1)
    for info in page.get_image_info():
        ir = pymupdf.Rect(info["bbox"]) & pr
        if not ir.is_empty:
            r |= ir
    try:
        for d in page.get_cdrawings():
            dr = pymupdf.Rect(d.get("rect", (0, 0, 0, 0))) & pr
            # fundos de página inteira não contam como conteúdo
            if not dr.is_empty and abs(dr) < 0.9 * abs(pr):
                r |= dr
    except Exception:  # noqa: BLE001
        pass
    return None if r.is_empty else r & pr


def compute_crop_boxes(doc: pymupdf.Document, indices: list[int], mode: str) -> dict[int, tuple]:
    """Corte de margens.

    ``uniform``: uma área para as páginas ímpares e outra para as pares (livros
    têm margem interna e externa espelhadas), assim a letra tem o mesmo tamanho
    em todas as páginas. Páginas sangradas (conteúdo até a borda) não são cortadas.
    ``page``: cada página com o seu próprio corte (texto maior, tamanho variável).
    """
    boxes: dict[int, pymupdf.Rect] = {}
    for i in indices:
        page = doc[i]
        r = _content_rect(page)
        if r is None:
            continue
        if abs(r) > 0.93 * abs(page.rect):
            continue  # sangrada ou cheia: mantém a página inteira
        pad = 0.018 * max(page.rect.width, page.rect.height)
        boxes[i] = (r + (-pad, -pad, pad, pad)) & page.rect
    if mode == "page":
        return {i: tuple(b) for i, b in boxes.items()}
    result: dict[int, tuple] = {}
    for parity in (0, 1):
        group = [i for i in boxes if i % 2 == parity]
        if not group:
            continue
        # união robusta: ignora 5% das páginas mais "largas" em cada borda
        def q(vals, frac):
            vals = sorted(vals)
            return vals[min(len(vals) - 1, int(frac * (len(vals) - 1)))]
        x0 = q([boxes[i].x0 for i in group], 0.05)
        y0 = q([boxes[i].y0 for i in group], 0.05)
        x1 = q([boxes[i].x1 for i in group], 0.95)
        y1 = q([boxes[i].y1 for i in group], 0.95)
        for i in group:
            union = pymupdf.Rect(x0, y0, x1, y1) | boxes[i]  # nunca corta conteúdo da página
            result[i] = tuple(union & doc[i].rect)
    return result


# -------------------------------------------------------------- capa/TOC
def render_cover(doc: pymupdf.Document, index: int, dest: Path, height: int = 1600) -> Path:
    page = doc[index]
    scale = min(height / page.rect.height, 2400 / page.rect.width)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    dest.write_bytes(pix.tobytes("jpeg", jpg_quality=88))
    return dest


def nest_toc(flat: list[tuple[int, str, str]]) -> list[TocEntry]:
    """[(nível 1.., título, href)] → árvore de TocEntry."""
    root: list[TocEntry] = []
    stack: list[tuple[int, TocEntry]] = []
    for level, title, href in flat:
        entry = TocEntry(title, href)
        while stack and stack[-1][0] >= level:
            stack.pop()
        (stack[-1][1].children if stack else root).append(entry)
        stack.append((level, entry))
    return root


def outline_entries(doc: pymupdf.Document) -> list[tuple[int, str, int]]:
    """Marcadores do PDF: (nível, título, índice da página 0-based)."""
    out = []
    for item in doc.get_toc(simple=True):
        level, title, page = item[0], T.clean_text(str(item[1]).strip()), int(item[2]) - 1
        if title and 0 <= page < doc.page_count:
            out.append((level, title, page))
    return out


def chapters_from_text(doc: pymupdf.Document, indices: list[int]) -> list[tuple[int, str, int]]:
    found = []
    for i in indices:
        blocks = [b for b in doc[i].get_text("blocks") if b[6] == 0 and b[4].strip()][:3]
        for b in blocks:
            first = T.clean_text(b[4].strip().splitlines()[0])
            if T.is_chapter_title(first):
                found.append((1, first[:90], i))
                break
    return found


def _page_href(first: dict[int, str], page: int) -> str | None:
    if page in first:
        return first[page]
    later = [p for p in first if p >= page]
    return first[min(later)] if later else None


# ------------------------------------------------------------------ plugin
class PdfInput(InputPlugin):
    format = "pdf"

    def __init__(self, path: Path):
        super().__init__(path)
        self.analysis: PdfAnalysis | None = None

    def read(self, ctx: InputContext) -> Book:
        opts = ctx.opts
        doc = open_pdf(ctx.source, opts.password)
        try:
            if doc.permissions and not (doc.permissions & pymupdf.PDF_PERM_COPY) and doc.is_encrypted:
                ctx.log("O PDF tem restrições de uso definidas pelo autor; respeite os direitos autorais.")
            book = Book(source_format="pdf")
            book.metadata = read_metadata(doc, ctx.original_name)
            indices = parse_page_range(opts.page_range, doc.page_count)
            ctx.progress(0.02, "Analisando o PDF")
            self.analysis = analyze(doc)
            mode = opts.mode
            if mode == "auto" and ctx.output_format == "pdf":
                mode = "fixed"
                book.notes.append("PDF para PDF: as páginas foram mantidas e só receberam os ajustes.")
            elif mode == "auto":
                mode = self.analysis.recommended
                book.notes.append("Modo escolhido automaticamente: "
                                  + ("layout fiel. " if mode == "fixed" else "texto fluido. ")
                                  + " ".join(self.analysis.reasons))
            ctx.log(f"Modo: {mode} · {len(indices)} página(s)")
            cover_idx = min(max(0, opts.cover_page - 1), doc.page_count - 1)
            cover_path = render_cover(doc, cover_idx, ctx.ws.sub("images") / "cover.jpg")
            book.cover = book.add_resource(Resource("images/cover.jpg", "image/jpeg", cover_path,
                                                    id="cover-image", properties={"cover-image"}))
            if mode == "fixed":
                self._read_fixed(doc, ctx, book, indices)
            else:
                from .pdf_reflow import build_reflow

                build_reflow(doc, ctx, book, indices)
            return book
        finally:
            doc.close()

    def _read_fixed(self, doc: pymupdf.Document, ctx: InputContext, book: Book, indices: list[int]) -> None:
        opts = ctx.opts
        gray = ctx.grayscale
        if gray and opts.temperature != NEUTRAL_K:
            book.warn("A tela de destino é monocromática: a temperatura não tem efeito em tons de cinza.")
        crop = {}
        if opts.trim_margins != "none":
            ctx.progress(0.04, "Calculando margens")
            crop = compute_crop_boxes(doc, indices, opts.trim_margins)
        img_dir = ctx.ws.sub("pages")
        cfg = RenderConfig(
            source=str(ctx.source), out_dir=str(img_dir), target=ctx.profile.size, dpi=opts.dpi,
            grayscale=gray, adjustments=Adjustments.from_options(opts).to_dict(),
            quality=opts.quality, fill_screen=bool(opts.fill_screen and ctx.profile.size),
            split_spreads=opts.split_spreads, rtl=book.direction == "rtl",
            password=opts.password, filetype="pdf", crop_boxes=crop,
        )
        pages = render_all(cfg, indices, workers=opts.workers,
                           progress=lambda f: ctx.progress(0.05 + 0.9 * f, "Renderizando páginas"),
                           cancelled=ctx.cancelled)
        first = populate_fixed_book(book, pages, img_dir, ctx.ws)
        flat = outline_entries(doc) or chapters_from_text(doc, indices)
        entries = []
        for level, title, page in flat:
            href = _page_href(first, page)
            if href:
                entries.append((level, title, href))
        if not entries and book.documents:
            entries = [(1, "Início", book.documents[0].href)]
        book.toc = nest_toc(entries)
        book.metadata.title = book.metadata.title or sanitize_filename(ctx.original_name)
