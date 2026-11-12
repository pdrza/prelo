"""Quadrinhos (CBZ/ZIP de imagens) e imagens soltas.

Recomendações HIGH (como o plugin de quadrinhos do Calibre força margens 0
e desliga a detecção de capítulos): layout fixo é obrigatório, porque o
conteúdo já é imagem.

Melhorias: ordem natural dos arquivos (``p2`` antes de ``p10``), leitura do
``ComicInfo.xml`` (título, série, autores e mangá da direita para a esquerda)
e subpastas viram capítulos do sumário.
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree
from PIL import Image

from ..core.errors import ConversionError
from ..core.formats import IMAGE_EXTS, is_junk, natural_key
from ..core.imaging import Adjustments, to_jpeg_bytes
from ..core.model import Book, Metadata, Resource
from ..core.options import Level, Recommendation
from ..core.safety import safe_extract
from .base import InputContext, InputPlugin, populate_fixed_book
from .epub import XML_PARSER
from .pdf import nest_toc
from .render import RenderConfig, process_images, render_all


def read_comicinfo(path: Path) -> tuple[Metadata, str]:
    md = Metadata()
    direction = "ltr"
    try:
        root = etree.fromstring(path.read_bytes(), XML_PARSER)
    except Exception:  # noqa: BLE001
        return md, direction
    if root is None:
        return md, direction

    def get(tag: str) -> str:
        el = root.find(tag)
        return (el.text or "").strip() if el is not None and el.text else ""

    series, number = get("Series"), get("Number")
    md.title = get("Title") or (f"{series} {number}".strip() if series else "")
    md.series = series
    try:
        md.series_index = float(number) if number else None
    except ValueError:
        md.series_index = None
    writers = ",".join(x for x in (get("Writer"), get("Penciller")) if x)
    md.authors = [a.strip() for a in writers.split(",") if a.strip()]
    md.description = get("Summary")
    md.publisher = get("Publisher")
    md.language = get("LanguageISO") or "pt"
    if get("Year"):
        md.date = get("Year")
    if get("Manga").lower() == "yesandrighttoleft":
        direction = "rtl"
    return md, direction


class ComicInput(InputPlugin):
    format = "comic"

    def recommendations(self) -> list[Recommendation]:
        return [
            Recommendation("mode", "fixed", Level.HIGH),
            Recommendation("split_spreads", True, Level.MED),
            Recommendation("trim_margins", "none", Level.MED),
        ]

    def _collect(self, ctx: InputContext) -> tuple[list[Path], Path | None, Path]:
        if self.format == "image":
            return [ctx.source], None, ctx.source.parent
        root = ctx.ws.sub("comic")
        safe_extract(ctx.source, root)
        files = []
        info = None
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if is_junk(rel):
                continue
            if p.name.lower() == "comicinfo.xml":
                info = p
            elif p.suffix.lower() in IMAGE_EXTS:
                files.append(p)
        files.sort(key=lambda p: natural_key(p.relative_to(root).as_posix()))
        return files, info, root

    def read(self, ctx: InputContext) -> Book:
        files, info, root = self._collect(ctx)
        if not files:
            raise ConversionError("Nenhuma imagem encontrada no arquivo.")
        book = Book(source_format=self.format)
        if info is not None:
            book.metadata, book.direction = read_comicinfo(info)
        if not book.metadata.title:
            book.metadata.title = re.sub(r"[_]+", " ", Path(ctx.original_name).stem)
        img_dir = ctx.ws.sub("pages")
        cfg = RenderConfig(
            source=str(ctx.source), out_dir=str(img_dir), target=ctx.profile.size, dpi=ctx.opts.dpi,
            grayscale=ctx.grayscale, adjustments=Adjustments.from_options(ctx.opts).to_dict(),
            quality=ctx.opts.quality, fill_screen=bool(ctx.opts.fill_screen and ctx.profile.size),
            split_spreads=ctx.opts.split_spreads, rtl=book.direction == "rtl",
        )
        work = [(i, str(p), str(i + 1)) for i, p in enumerate(files)]
        pages = render_all(cfg, work, workers=ctx.opts.workers, fn=process_images,
                           progress=lambda f: ctx.progress(0.05 + 0.9 * f, "Processando imagens"),
                           cancelled=ctx.cancelled)
        first = populate_fixed_book(book, pages, img_dir, ctx.ws)
        # capa colorida a partir da primeira imagem original
        cover_path = ctx.ws.sub("images") / "cover.jpg"
        cover_path.write_bytes(to_jpeg_bytes(files[0].read_bytes(), 88, max_side=1600))
        book.cover = book.add_resource(Resource("images/cover.jpg", "image/jpeg", cover_path,
                                                id="cover-image", properties={"cover-image"}))
        # subpastas = capítulos
        entries = []
        last_folder = None
        for i, p in enumerate(files):
            folder = p.parent.relative_to(root).as_posix() if self.format != "image" else ""
            if folder not in ("", ".") and folder != last_folder and i in first:
                entries.append((1, Path(folder).name, first[i]))
            last_folder = folder
        book.toc = nest_toc(entries if len(entries) >= 2 else [(1, "Início", book.documents[0].href)])
        if book.direction == "rtl":
            book.notes.append("Mangá detectado (ComicInfo.xml): leitura da direita para a esquerda.")
        return book


class ImageInput(ComicInput):
    format = "image"

    def recommendations(self) -> list[Recommendation]:
        return [Recommendation("mode", "fixed", Level.HIGH)]


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size
