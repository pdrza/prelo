"""Interface comum dos leitores (plugins de entrada)."""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.model import Book, Document, PageTarget, Resource
from ..core.options import OptionSet, Recommendation
from ..core.profiles import DeviceProfile
from ..core.safety import Workspace

FIXED_CSS = """@page { margin: 0; }
html, body { margin: 0; padding: 0; }
body.fxl { overflow: hidden; }
div.pg { margin: 0; padding: 0; width: 100%; height: 100%; text-align: center; }
div.pg img { display: block; width: 100%; height: 100%; margin: 0; padding: 0; border: 0; }
"""


@dataclass
class InputContext:
    source: Path
    original_name: str
    opts: OptionSet
    profile: DeviceProfile
    output_format: str
    ws: Workspace
    progress: Callable[[float, str], None]
    cancelled: Callable[[], bool]
    log: Callable[[str], None]

    @property
    def grayscale(self) -> bool:
        mode = self.opts.color_mode
        if mode == "auto":
            return self.profile.eink and not self.profile.color
        return mode == "grayscale"


class InputPlugin:
    format = ""

    def __init__(self, path: Path):
        self.path = path

    def recommendations(self) -> list[Recommendation]:
        return []

    def read(self, ctx: InputContext) -> Book:  # pragma: no cover - interface
        raise NotImplementedError


def add_stylesheet(book: Book, ws: Workspace, name: str, css: str) -> str:
    path = ws.sub("styles") / name
    path.write_text(css, encoding="utf-8")
    href = f"styles/{name}"
    book.add_resource(Resource(href, "text/css", path))
    if href not in book.stylesheets:
        book.stylesheets.append(href)
    return href


def populate_fixed_book(book: Book, pages, img_dir: Path, ws: Workspace, *, title_prefix: str = "Página") -> dict[int, str]:
    """Transforma páginas renderizadas em documentos de layout fixo.

    Devolve o mapa índice da página de origem → href do primeiro documento dela
    (para o sumário apontar para a página certa)."""
    add_stylesheet(book, ws, "fixed.css", FIXED_CSS)
    first: dict[int, str] = {}
    sizes: Counter = Counter()
    for p in pages:
        img_href = f"images/{p.filename}"
        book.add_resource(Resource(img_href, p.media_type, img_dir / p.filename))
        stem = p.filename.rsplit(".", 1)[0]
        doc_href = f"text/{stem}.xhtml"
        label = html.escape(p.label)
        body = (f'<div class="pg"><img src="../{img_href}" alt="{title_prefix} {label}" '
                f'width="{p.width}" height="{p.height}"/></div>')
        book.documents.append(Document(
            href=doc_href, title=f"{title_prefix} {p.label}", body=body, id=f"d{stem}",
            viewport=(p.width, p.height), body_attrs={"class": "fxl"},
            source_page=p.index, source_size=p.source_size,
        ))
        sizes[(p.width, p.height)] += 1
        if p.index not in first:
            first[p.index] = doc_href
            book.page_list.append(PageTarget(p.label, doc_href))
    book.layout = "fixed"
    if sizes:
        book.viewport = sizes.most_common(1)[0][0]
    return first
