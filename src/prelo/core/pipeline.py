"""O pipeline de conversão (o "plumber").

Etapas e faixas de progresso, como no Calibre:

====================  ==========
preparo               0–3 %       detecta o formato pelo conteúdo, recusa DRM, copia a entrada
entrada               3–70 %      leitor gera o livro intermediário
transformações        70–76 %     metadados, sumário, capa, limpeza de arquivos órfãos
saída                 76–98 %     escritor grava o formato pedido
final                 98–100 %    gravação atômica, nunca sobrescreve
====================  ==========

Com ``debug_pipeline`` o livro é salvo depois da entrada e depois das
transformações (como ``--debug-pipeline`` do Calibre), para achar em que
etapa um problema aparece.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .. import APP_NAME, __version__
from ..inputs.base import InputContext
from .errors import CancelledError, IncompatibleConversionError
from .formats import OUTPUTS, check_compatible, detect_format
from .model import Book, TocEntry, resolve_href
from .options import Level, OptionSet, Recommendation
from .profiles import get_profile
from .safety import AtomicOutput, Workspace, unique_path

STAGES = {
    "preparo": (0.0, 0.03),
    "entrada": (0.03, 0.70),
    "transformações": (0.70, 0.76),
    "saída": (0.76, 0.98),
    "final": (0.98, 1.0),
}

ProgressFn = Callable[[str, float, str], None]


@dataclass
class ConversionRequest:
    input_path: Path
    output_format: str
    output_dir: Path | None = None
    output_name: str | None = None
    options: dict = field(default_factory=dict)         # escolha na hora: HIGH
    book_options: dict = field(default_factory=dict)    # salva para o livro: HIGH
    saved_defaults: dict = field(default_factory=dict)  # preferências: LOW
    metadata: dict = field(default_factory=dict)
    from_network: bool = False


@dataclass
class ConversionResult:
    output_path: str
    output_format: str
    layout: str
    documents: int
    size: int
    title: str
    seconds: float
    warnings: list[str]
    notes: list[str]
    options: list[dict]
    option_notes: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def input_plugin(fmt: str, path: Path):
    if fmt == "pdf":
        from ..inputs.pdf import PdfInput
        return PdfInput(path)
    if fmt == "epub":
        from ..inputs.epub import EpubInput
        return EpubInput(path)
    if fmt == "mobi":
        from ..inputs.mobi import MobiInput
        return MobiInput(path)
    if fmt == "comic":
        from ..inputs.images import ComicInput
        return ComicInput(path)
    from ..inputs.images import ImageInput
    return ImageInput(path)


def output_recommendations(input_fmt: str, output_fmt: str) -> list[Recommendation]:
    recs = []
    if output_fmt == "cbz":
        recs.append(Recommendation("mode", "fixed", Level.HIGH))
    if output_fmt == "txt":
        recs.append(Recommendation("mode", "reflow", Level.HIGH))
    return recs


def _chosen(name: str, *layers: dict) -> object | None:
    for layer in layers:
        if layer and layer.get(name) not in (None, ""):
            return layer[name]
    return None


def build_options(req: ConversionRequest, input_fmt: str, plugin) -> tuple[OptionSet, object]:
    profile_id = _chosen("profile", req.options, req.book_options, req.saved_defaults) or "auto"
    profile = get_profile(str(profile_id), req.output_format)
    opts = OptionSet()
    opts.recommend(plugin.recommendations(), f"entrada {input_fmt.upper()}")
    opts.recommend(output_recommendations(input_fmt, req.output_format),
                   f"saída {OUTPUTS[req.output_format].label}")
    opts.recommend(profile.recommendations(), f"dispositivo {profile.name}")
    opts.apply(req.saved_defaults, Level.LOW, "preferências")
    opts.apply(req.book_options, Level.HIGH, "configuração do livro")
    opts.apply(req.options, Level.HIGH, "escolha atual", from_network=req.from_network)
    if input_fmt in ("comic", "image") and opts.mode != "fixed":
        raise IncompatibleConversionError("Quadrinhos e imagens só podem ser convertidos em layout fiel.")
    return opts, profile


# --------------------------------------------------------- transformações
_REF = re.compile(r'(?:src|href|xlink:href)\s*=\s*"([^"#?]+)', re.I)
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)


def remove_unreferenced(book: Book) -> int:
    """Remove arquivos que nada referencia (transformação 15 do Calibre)."""
    used: set[str] = set(book.stylesheets)
    if book.cover:
        used.add(book.cover.href)
    for d in book.documents:
        used.update(resolve_href(d.href, m) for m in _REF.findall(d.body) if "://" not in m)
        used.update(d.stylesheets)
        used.update(resolve_href(d.href, m) for m in _CSS_URL.findall(d.inline_css) if "://" not in m)
    for r in book.resources:
        if r.is_css and r.href in used:
            css = r.path.read_text("utf-8", "replace")
            used.update(resolve_href(r.href, m) for m in _CSS_URL.findall(css) if "://" not in m)
    before = len(book.resources)
    book.resources = [r for r in book.resources if r.href in used]
    return before - len(book.resources)


def ensure_toc(book: Book) -> None:
    hrefs = {d.href for d in book.documents}
    def valid(entries: list[TocEntry]) -> list[TocEntry]:
        out = []
        for e in entries:
            e.children = valid(e.children)
            if e.href.split("#")[0] in hrefs:
                out.append(e)
            else:
                out.extend(e.children)
        return out
    book.toc = valid(book.toc)
    if not book.toc and book.documents:
        book.toc = [TocEntry("Início", book.documents[0].href)]  # regra do Calibre: entrada única


def _snapshot(book: Book, folder: Path, name: str, opts: OptionSet) -> None:
    from ..outputs.epub import write_epub

    folder.mkdir(parents=True, exist_ok=True)
    write_epub(book, folder / f"{name}.epub")
    (folder / f"{name}-opcoes.json").write_text(
        json.dumps({"opcoes": opts.report(), "avisos": book.warnings, "notas": book.notes},
                   ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ saída
def write_output(book: Book, tmp: Path, fmt: str, opts: OptionSet, profile, source: Path, log) -> None:
    if fmt == "epub":
        from ..outputs.epub import write_epub
        write_epub(book, tmp)
    elif fmt in ("azw3", "mobi"):
        if opts.kindle_engine == "calibre":
            from ..outputs.calibre_engine import write_with_calibre
            write_with_calibre(book, tmp, fmt, log)
        elif fmt == "azw3":
            from ..outputs.kf8 import write_azw3
            write_azw3(book, tmp, opts, log=log)
        else:
            from ..outputs.mobi import write_mobi
            write_mobi(book, tmp, opts, log=log)
    elif fmt == "cbz":
        from ..outputs.simple import write_cbz
        write_cbz(book, tmp)
    elif fmt == "pdf":
        from ..outputs.simple import write_pdf
        write_pdf(book, tmp, source_pdf=source if book.source_format == "pdf" else None,
                  text_layer=opts.pdf_text_layer, page_size=profile.size, log=log)
    elif fmt == "txt":
        from ..outputs.simple import write_txt
        write_txt(book, tmp)
    else:  # pragma: no cover
        raise IncompatibleConversionError(f"Saída desconhecida: {fmt}")


def convert(req: ConversionRequest, *, progress: ProgressFn | None = None,
            cancelled: Callable[[], bool] | None = None, log: Callable[[str], None] | None = None) -> ConversionResult:
    t0 = time.time()
    log = log or (lambda m: None)
    cancelled = cancelled or (lambda: False)

    def report(stage: str, frac: float, msg: str) -> None:
        lo, hi = STAGES[stage]
        if progress:
            progress(stage, lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)

    def check() -> None:
        if cancelled():
            raise CancelledError("Conversão cancelada.")

    src = Path(req.input_path)
    fmt_out = req.output_format.lower()
    report("preparo", 0.1, "Identificando o arquivo")
    fmt_in = detect_format(src)
    check_compatible(fmt_in, fmt_out)
    log(f"{APP_NAME} {__version__} · entrada {fmt_in} · saída {fmt_out}")

    plugin = input_plugin(fmt_in, src)
    opts, profile = build_options(req, fmt_in, plugin)
    debug_dir = Path(opts.debug_pipeline) if opts.debug_pipeline else None
    ws = Workspace(keep=bool(debug_dir))
    try:
        local = ws.copy_input(src)  # o original nunca é tocado
        plugin.path = local
        report("preparo", 1.0, "Arquivo copiado")
        check()

        ctx = InputContext(source=local, original_name=src.name, opts=opts, profile=profile,
                           output_format=fmt_out, ws=ws, progress=lambda f, m: report("entrada", f, m),
                           cancelled=cancelled, log=log)
        book = plugin.read(ctx)
        check()
        if debug_dir:
            _snapshot(book, debug_dir, "1-entrada", opts)

        report("transformações", 0.2, "Aplicando metadados")
        changed = book.metadata.apply_overrides(req.metadata)
        if changed:
            log("Metadados editados: " + ", ".join(changed))
        if not book.metadata.title:
            book.metadata.title = src.stem
        ensure_toc(book)
        removed = remove_unreferenced(book)
        if removed:
            log(f"{removed} arquivo(s) sem referência removido(s).")
        if debug_dir:
            _snapshot(book, debug_dir, "2-processado", opts)
        report("transformações", 1.0, "Livro pronto para gravar")
        check()

        out_dir = Path(req.output_dir) if req.output_dir else src.parent
        stem = req.output_name or book.metadata.title or src.stem
        dest = unique_path(out_dir, stem, OUTPUTS[fmt_out].ext)  # nunca sobrescreve
        report("saída", 0.05, f"Gravando {OUTPUTS[fmt_out].label}")
        writer = AtomicOutput(dest)
        with writer as tmp:
            write_output(book, tmp, fmt_out, opts, profile, local, log)
            check()
        final_path = writer.final_path
        report("final", 1.0, "Concluído")
        for w in book.warnings:
            log("Aviso: " + w)
        return ConversionResult(
            output_path=str(final_path), output_format=fmt_out, layout=book.layout,
            documents=len(book.documents), size=final_path.stat().st_size,
            title=book.metadata.title, seconds=round(time.time() - t0, 2),
            warnings=book.warnings, notes=book.notes, options=opts.report(), option_notes=opts.notes,
        )
    finally:
        ws.cleanup()
