"""PDFs e arquivos de teste gerados na hora (sem material de terceiros).

Cada gerador cria um caso que costuma quebrar conversores de PDF:
cabeçalhos e rodapés correntes, hifenização no fim da linha, parágrafo que
continua na página seguinte, tabela, imagem, nota de rodapé, duas colunas.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

A5 = pymupdf.paper_rect("a5")
WORDS = ("a luz da tarde atravessava a janela da biblioteca enquanto os livros esperavam em "
         "silêncio pela próxima leitura e cada página guardava uma conversa antiga entre quem "
         "escreveu e quem ainda vai ler com calma o que ficou registrado no papel amarelado").split()

LONG_WORDS = ["extraordinariamente", "conversação", "transformação", "acontecimentos",
              "biblioteca", "simplesmente", "inesperadamente", "imaginação"]


def paragraph(n: int, seed: int) -> str:
    out = []
    for i in range(n):
        w = WORDS[(seed * 7 + i * 3) % len(WORDS)]
        if i % 9 == 4:
            w = LONG_WORDS[(seed + i) % len(LONG_WORDS)]
        out.append(w)
    out[0] = out[0].capitalize()
    return " ".join(out) + "."


def layout_lines(text: str, width: float, size: float = 10.5, font: str = "tiro") -> list[str]:
    """Quebra em linhas como um diagramador: palavras longas são hifenizadas."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if pymupdf.get_text_length(trial, fontname=font, fontsize=size) <= width:
            cur = trial
            continue
        if len(word) >= 9 and cur:
            for cut in range(len(word) - 3, 3, -1):
                head = f"{cur} {word[:cut]}-"
                if pymupdf.get_text_length(head, fontname=font, fontsize=size) <= width:
                    lines.append(head)
                    cur = word[cut:]
                    break
            else:
                lines.append(cur)
                cur = word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _png(color=(200, 80, 60), size=(320, 200)) -> bytes:
    im = Image.new("RGB", size, (245, 240, 230))
    d = ImageDraw.Draw(im)
    d.ellipse((40, 30, 280, 170), fill=color)
    d.rectangle((10, 10, size[0] - 10, size[1] - 10), outline=(30, 30, 30), width=4)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Writer:
    def __init__(self, doc: pymupdf.Document, title: str):
        self.doc, self.title = doc, title
        self.page: pymupdf.Page | None = None
        self.y = 0.0
        self.margin = 48
        self.pno = 0

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=A5.width, height=A5.height)
        self.pno += 1
        self.page.insert_text((self.margin, 30), self.title.upper(), fontname="tiro", fontsize=8)
        self.page.insert_text((A5.width / 2 - 6, A5.height - 22), str(self.pno), fontname="tiro", fontsize=9)
        self.y = 62

    def ensure(self, h: float) -> None:
        if self.page is None or self.y + h > A5.height - 48:
            self.new_page()

    def heading(self, text: str, size: float = 18) -> None:
        self.ensure(60)
        self.y += 14
        self.page.insert_text((self.margin, self.y + size), text, fontname="tibo", fontsize=size)
        self.y += size + 18

    def para(self, text: str, size: float = 10.5) -> None:
        width = A5.width - 2 * self.margin
        lines = layout_lines(text, width - 14, size)
        for k, line in enumerate(lines):
            self.ensure(size * 1.45)
            x = self.margin + (14 if k == 0 else 0)
            self.page.insert_text((x, self.y + size), line, fontname="tiro", fontsize=size)
            self.y += size * 1.45
        self.y += 2

    def image(self, data: bytes, h: float = 110) -> None:
        self.ensure(h + 10)
        r = pymupdf.Rect(self.margin + 40, self.y, A5.width - self.margin - 40, self.y + h)
        self.page.insert_image(r, stream=data)
        self.y += h + 12

    def table(self, rows: list[list[str]]) -> None:
        h = 18 * len(rows)
        self.ensure(h + 12)
        x0, x1 = self.margin, A5.width - self.margin
        cols = len(rows[0])
        cw = (x1 - x0) / cols
        shape = self.page.new_shape()
        for r in range(len(rows) + 1):
            shape.draw_line((x0, self.y + r * 18), (x1, self.y + r * 18))
        for c in range(cols + 1):
            shape.draw_line((x0 + c * cw, self.y), (x0 + c * cw, self.y + h))
        shape.finish(color=(0, 0, 0), width=0.6)
        shape.commit()
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                self.page.insert_text((x0 + c * cw + 4, self.y + r * 18 + 13), cell,
                                      fontname="tibo" if r == 0 else "tiro", fontsize=9)
        self.y += h + 14


def make_novel_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    w = Writer(doc, "O livro de teste")
    w.new_page()
    w.page.insert_text((w.margin, 200), "O Livro de Teste", fontname="tibo", fontsize=26)
    w.page.insert_text((w.margin, 240), "Autora Exemplo", fontname="tiro", fontsize=14)
    toc = []
    for ch in range(1, 4):
        w.new_page()
        toc.append([1, f"Capítulo {ch}", w.pno])
        w.heading(f"Capítulo {ch}")
        for p in range(6):
            w.para(paragraph(70 + p * 9, ch * 10 + p))
            if ch == 1 and p == 1:
                w.para("Levou o guarda-chuva e disse-lhe que voltaria cedo para a conversa na "
                       "biblioteca, onde o guarda-chuva ficaria seco.")
            if ch == 2 and p == 2:
                w.image(_png())
            if ch == 3 and p == 1:
                w.table([["Ano", "Livros", "Leitores"], ["2024", "120", "3.400"], ["2025", "180", "5.100"]])
    last = doc[-1]
    last.insert_text((w.margin, A5.height - 60), "1 Nota de rodapé com a fonte da citação.",
                     fontname="tiro", fontsize=7.5)
    doc.set_toc(toc)
    doc.set_metadata({"title": "O Livro de Teste", "author": "Autora Exemplo"})
    doc.save(path)
    doc.close()
    return path


def make_magazine_pdf(path: Path, pages: int = 4) -> Path:
    """Duas colunas com imagens: a análise automática deve escolher layout fiel."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((40, 60), f"Revista de Teste · edição {n + 1}", fontname="hebo", fontsize=20)
        for col in range(2):
            x0 = 40 + col * 270
            rect = pymupdf.Rect(x0, 90, x0 + 250, 560)
            page.insert_textbox(rect, " ".join(paragraph(60, n * 3 + col + k) for k in range(5)),
                                fontname="helv", fontsize=9)
        page.insert_image(pymupdf.Rect(40, 580, 555, 800), stream=_png((40, 90, 200), (640, 300)))
    doc.save(path)
    doc.close()
    return path


def make_scanned_pdf(path: Path, pages: int = 3) -> Path:
    doc = pymupdf.open()
    for n in range(pages):
        im = Image.new("L", (1240, 1754), 235)
        d = ImageDraw.Draw(im)
        for row in range(40):
            d.rectangle((120, 150 + row * 36, 1100 - (row % 5) * 60, 168 + row * 36), fill=40)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


def make_cbz(path: Path, pages: int = 4) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for n in range(pages):
            size = (1600, 1100) if n == 2 else (800, 1200)  # uma página dupla
            im = Image.new("RGB", size, (250, 250, 250))
            d = ImageDraw.Draw(im)
            d.text((40, 40), f"Página {n + 1}", fill=(0, 0, 0))
            d.rectangle((60, 120, size[0] - 60, size[1] - 60), outline=(0, 0, 0), width=6)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            zf.writestr(f"cap1/pag{n + 1}.png", buf.getvalue())
        zf.writestr("ComicInfo.xml", "<ComicInfo><Title>Quadrinho de Teste</Title><Series>Teste</Series>"
                                     "<Number>1</Number><Writer>Fulana</Writer><Manga>No</Manga></ComicInfo>")
    return path


def make_epub(path: Path) -> Path:
    """EPUB 2 mínimo, com &nbsp; (entidade HTML) e NCX."""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:title>Livro EPUB de Teste</dc:title><dc:creator>Autor Teste</dc:creator><dc:language>pt-BR</dc:language>
<dc:identifier id="uid">urn:uuid:12345678-1234-1234-1234-123456789abc</dc:identifier>
<meta name="cover" content="capa"/>
</metadata>
<manifest>
<item id="c1" href="Text/cap1.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="Text/cap2.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="Styles/estilo.css" media-type="text/css"/>
<item id="capa" href="Images/capa.png" media-type="image/png"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>x</text></docTitle>
<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Primeiro</text></navLabel><content src="Text/cap1.xhtml"/></navPoint>
<navPoint id="n2" playOrder="2"><navLabel><text>Segundo</text></navLabel><content src="Text/cap2.xhtml#s2"/></navPoint></navMap></ncx>"""
    chap = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{t}</title>
<link rel="stylesheet" type="text/css" href="../Styles/estilo.css"/></head>
<body><h1>{t}</h1><p>Texto&nbsp;com espaço fixo e <em>ênfase</em>.</p>{extra}
<p id="s2">Segunda seção com <a href="cap1.xhtml">link de volta</a>.</p></body></html>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                    'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", ncx)
        zf.writestr("OEBPS/Styles/estilo.css", "h1{text-align:center} p{text-indent:1em}")
        zf.writestr("OEBPS/Images/capa.png", _png((90, 140, 90), (600, 900)))
        zf.writestr("OEBPS/Text/cap1.xhtml", chap.format(t="Capítulo Um", extra='<p><img src="../Images/capa.png" alt=""/></p>'))
        zf.writestr("OEBPS/Text/cap2.xhtml", chap.format(t="Capítulo Dois", extra=""))
    return path


def make_long_epub(path: Path, chapters: int = 4, paragraphs: int = 30) -> Path:
    """EPUB com capítulos longos, sumário (NCX) e um link interno: a paginação muda com a letra."""
    items = "".join(f'<item id="c{n}" href="Text/cap{n}.xhtml" media-type="application/xhtml+xml"/>'
                    for n in range(1, chapters + 1))
    spine = "".join(f'<itemref idref="c{n}"/>' for n in range(1, chapters + 1))
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Livro Longo</dc:title>
<dc:creator>Autora Teste</dc:creator><dc:language>pt-BR</dc:language>
<dc:identifier id="uid">urn:uuid:87654321-4321-4321-4321-cba987654321</dc:identifier></metadata>
<manifest>{items}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>
<spine toc="ncx">{spine}</spine></package>"""
    points = "".join(f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>Capítulo {n}</text></navLabel>'
                     f'<content src="Text/cap{n}.xhtml"/></navPoint>' for n in range(1, chapters + 1))
    ncx = ('<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f"<head/><docTitle><text>Livro Longo</text></docTitle><navMap>{points}</navMap></ncx>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                    'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", ncx)
        for n in range(1, chapters + 1):
            body = "".join(f"<p>{paragraph(70, n * 100 + i)}</p>" for i in range(paragraphs))
            link = f'<p>Veja o <a href="cap{chapters}.xhtml">último capítulo</a>.</p>' if n == 1 else ""
            zf.writestr(f"OEBPS/Text/cap{n}.xhtml",
                        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                        f"<head><title>Capítulo {n}</title></head><body><h1>Capítulo {n}</h1>{link}{body}</body></html>")
    return path
