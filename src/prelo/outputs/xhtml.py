"""Montagem de documentos XHTML a partir do livro intermediário."""

from __future__ import annotations

import html
import re
from html.entities import name2codepoint

from ..core.model import Book, Document, relative_href

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
_SELF_CLOSING = re.compile(r"<([a-zA-Z][\w:.-]*)([^<>]*)/>")
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]{1,31});")
XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def numeric_entities(text: str) -> str:
    """&nbsp; e companhia viram &#160;: XML não conhece entidades do HTML."""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in XML_ENTITIES:
            return m.group(0)
        cp = name2codepoint.get(name)
        return f"&#{cp};" if cp else m.group(0)
    return _ENTITY.sub(repl, text)


def close_non_void(markup: str) -> str:
    """``<div/>`` vira ``<div></div>`` (em HTML5, ``<div/>`` não fecha a tag)."""
    def repl(m: re.Match) -> str:
        tag, attrs = m.group(1), (m.group(2) or "").rstrip()
        if tag.split(":")[-1].lower() in VOID or ":" in tag:
            return m.group(0)
        return f"<{tag}{attrs}></{tag}>"
    return _SELF_CLOSING.sub(repl, markup)


def stylesheets_for(doc: Document, book: Book) -> list[str]:
    seen = list(doc.stylesheets)
    for s in book.stylesheets:
        if s not in seen:
            seen.append(s)
    return seen


def body_attributes(doc: Document) -> str:
    attrs = dict(doc.body_attrs)
    if doc.viewport and "style" not in attrs:
        w, h = doc.viewport
        attrs["style"] = f"width:{w}px;height:{h}px;margin:0;padding:0"
    return "".join(f' {k}="{esc(v)}"' for k, v in attrs.items() if re.fullmatch(r"[A-Za-z_:][\w:.-]*", k))


def build_xhtml(doc: Document, book: Book, *, css_hrefs: list[str] | None = None,
                extra_head: str = "") -> str:
    lang = esc(doc.lang or book.metadata.language or "pt")
    head = ['<meta charset="utf-8"/>', f"<title>{esc(doc.title or book.metadata.title)}</title>"]
    if doc.viewport:
        w, h = doc.viewport
        head.append(f'<meta name="viewport" content="width={w}, height={h}"/>')
    for css in css_hrefs if css_hrefs is not None else stylesheets_for(doc, book):
        head.append(f'<link rel="stylesheet" type="text/css" href="{esc(relative_href(doc.href, css))}"/>')
    if doc.inline_css.strip():
        css = html.escape(doc.inline_css, quote=False)  # XHTML: & e < precisam ser escapados
        head.append(f"<style>{css}</style>")
    if extra_head:
        head.append(extra_head)
    body = close_non_void(numeric_entities(doc.body))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{lang}" xml:lang="{lang}">\n<head>\n' + "\n".join(head) + "\n</head>\n"
        f"<body{body_attributes(doc)}>\n{body}\n</body>\n</html>\n"
    )
