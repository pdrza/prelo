"""Livro intermediário.

Regra herdada do Calibre: nenhum formato é convertido direto em outro. Todo
leitor gera um :class:`Book` e todo escritor parte de um :class:`Book`. Assim
qualquer entrada combina com qualquer saída sem um conversor por par, e as
transformações (metadados, sumário, ajustes de página) são escritas uma vez só.

Diferença em relação ao OEB do Calibre: o livro sabe se é de **layout fixo**
(cada página é uma página, como no PDF) ou **fluido** (o texto se adapta à
tela). Isso permite levar um PDF para AZW3/EPUB sem quebrar a diagramação.
"""

from __future__ import annotations

import posixpath
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

Layout = Literal["reflow", "fixed"]
Direction = Literal["ltr", "rtl"]


@dataclass
class Metadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    language: str = "pt"
    publisher: str = ""
    description: str = ""
    isbn: str = ""
    subjects: list[str] = field(default_factory=list)
    date: str = ""
    series: str = ""
    series_index: float | None = None
    rights: str = ""
    identifier: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")

    EDITABLE = (
        "title", "authors", "language", "publisher", "description", "isbn",
        "subjects", "date", "series", "series_index", "rights",
    )

    def apply_overrides(self, overrides: dict | None) -> list[str]:
        """Aplica os metadados editados pelo usuário. Valores vazios não apagam
        o que veio do arquivo. Devolve a lista de campos alterados."""
        changed: list[str] = []
        for key, value in (overrides or {}).items():
            if key not in self.EDITABLE or value in (None, "", []):
                continue
            if key in ("authors", "subjects") and isinstance(value, str):
                value = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
            if key == "series_index":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed.append(key)
        return changed

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (*self.EDITABLE, "identifier")}


@dataclass
class Resource:
    """Arquivo que não é página: imagem, CSS, fonte. Os bytes ficam em disco
    (``path``), nunca na memória, para livros grandes não estourarem a RAM."""

    href: str
    media_type: str
    path: Path
    id: str = ""
    properties: set[str] = field(default_factory=set)

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    @property
    def is_image(self) -> bool:
        return self.media_type.startswith("image/")

    @property
    def is_css(self) -> bool:
        return self.media_type == "text/css"

    @property
    def is_font(self) -> bool:
        return self.media_type.startswith(("font/", "application/font", "application/x-font",
                                           "application/vnd.ms-opentype"))


@dataclass
class Document:
    """Uma página/capítulo XHTML na ordem de leitura.

    ``body`` guarda só o conteúdo interno do ``<body>`` (XHTML bem formado).
    Cada escritor monta o ``<head>`` do jeito que o formato exige.
    """

    href: str
    title: str
    body: str
    id: str = ""
    linear: bool = True
    properties: set[str] = field(default_factory=set)
    viewport: tuple[int, int] | None = None
    body_attrs: dict[str, str] = field(default_factory=dict)
    stylesheets: list[str] = field(default_factory=list)
    inline_css: str = ""
    lang: str | None = None
    # Página de origem (PDF), em pontos: usado pela saída PDF para manter o tamanho.
    source_page: int | None = None
    source_size: tuple[float, float] | None = None

    @property
    def dir(self) -> str:
        return posixpath.dirname(self.href)


@dataclass
class TocEntry:
    title: str
    href: str
    children: list["TocEntry"] = field(default_factory=list)

    def walk(self, depth: int = 0) -> Iterator[tuple[int, "TocEntry"]]:
        yield depth, self
        for child in self.children:
            yield from child.walk(depth + 1)


@dataclass
class PageTarget:
    """Número de página do original (vira page-list no EPUB 3)."""

    label: str
    href: str


@dataclass
class Book:
    metadata: Metadata = field(default_factory=Metadata)
    layout: Layout = "reflow"
    direction: Direction = "ltr"
    documents: list[Document] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)
    page_list: list[PageTarget] = field(default_factory=list)
    cover: Resource | None = None
    viewport: tuple[int, int] | None = None
    stylesheets: list[str] = field(default_factory=list)
    book_type: str | None = None
    source_format: str = ""
    warnings: list[str] = field(default_factory=list)
    # Informações para a interface ("por que escolhemos este modo").
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def resource(self, href: str) -> Resource | None:
        for res in self.resources:
            if res.href == href:
                return res
        return None

    def document(self, href: str) -> Document | None:
        for doc in self.documents:
            if doc.href == href:
                return doc
        return None

    def add_resource(self, res: Resource) -> Resource:
        existing = self.resource(res.href)
        if existing is not None:
            return existing
        if not res.id:
            res.id = f"r{len(self.resources) + 1:04d}"
        self.resources.append(res)
        return res

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def iter_toc(self) -> Iterator[tuple[int, TocEntry]]:
        for entry in self.toc:
            yield from entry.walk()

    def toc_count(self) -> int:
        return sum(1 for _ in self.iter_toc())


def resolve_href(base_doc_href: str, link: str) -> str:
    """Resolve um link relativo dentro do livro (``../img/a.jpg`` a partir de
    ``text/c1.xhtml`` vira ``img/a.jpg``). Mantém o fragmento ``#id``."""
    path, _, frag = link.partition("#")
    if not path:
        resolved = base_doc_href
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_doc_href), path))
        if resolved.startswith("./"):
            resolved = resolved[2:]
    return f"{resolved}#{frag}" if frag else resolved


def relative_href(from_doc_href: str, target_href: str) -> str:
    """Caminho relativo de ``from_doc_href`` até ``target_href``."""
    path, _, frag = target_href.partition("#")
    start = posixpath.dirname(from_doc_href) or "."
    rel = posixpath.relpath(path, start) if path else ""
    return f"{rel}#{frag}" if frag else rel
