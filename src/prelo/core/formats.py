"""Formatos suportados e detecção.

O Calibre detecta o formato pela extensão do arquivo. Aqui o formato é
detectado pelo **conteúdo** (assinatura dos primeiros bytes), e a extensão só
desempata. Um ``livro.pdf`` que na verdade é um EPUB é tratado como EPUB.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .errors import DrmError, IncompatibleConversionError, UnsupportedFormatError

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def natural_key(name: str) -> list:
    """Ordem natural: ``p2`` antes de ``p10``."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def is_junk(name: str) -> bool:
    """Arquivo que não é página: oculto (inclusive os ``._`` que o macOS cria ao
    compactar), dentro de ``__MACOSX`` ou o ``Thumbs.db`` do Windows."""
    parts = name.replace("\\", "/").split("/")
    return any(p.startswith(".") or p == "__MACOSX" for p in parts) or parts[-1].lower() == "thumbs.db"


def comic_pages(names: Iterable[str]) -> list[str]:
    """As páginas de um quadrinho, a partir dos nomes dentro do ZIP, em ordem natural.

    Estante, prévia e leitor contam as páginas por aqui, então mostram o mesmo número."""
    return sorted((n for n in names if Path(n).suffix.lower() in IMAGE_EXTS and not is_junk(n)), key=natural_key)


@dataclass(frozen=True)
class OutputFormat:
    id: str
    label: str
    ext: str
    layouts: frozenset[str]
    description: str
    media_type: str

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "ext": self.ext,
                "layouts": sorted(self.layouts), "description": self.description}


OUTPUTS: dict[str, OutputFormat] = {
    f.id: f
    for f in (
        OutputFormat("epub", "EPUB", ".epub", frozenset({"fixed", "reflow"}),
                     "Padrão aberto. Kobo, Apple Books, Google Play, PocketBook e Envio para Kindle.",
                     "application/epub+zip"),
        OutputFormat("azw3", "AZW3", ".azw3", frozenset({"fixed", "reflow"}),
                     "Formato Kindle moderno (KF8). Copie pelo cabo USB para a pasta documents.",
                     "application/vnd.amazon.ebook"),
        OutputFormat("mobi", "MOBI", ".mobi", frozenset({"fixed", "reflow"}),
                     "Kindle antigo (MOBI 6). Compatível com todos os Kindles, sem CSS.",
                     "application/x-mobipocket-ebook"),
        OutputFormat("cbz", "CBZ", ".cbz", frozenset({"fixed"}),
                     "Páginas como imagens. Ideal para quadrinhos e leitores de imagem.",
                     "application/vnd.comicbook+zip"),
        OutputFormat("pdf", "PDF", ".pdf", frozenset({"fixed", "reflow"}),
                     "PDF com as páginas ajustadas (temperatura, margens) ou paginado de novo.",
                     "application/pdf"),
        OutputFormat("txt", "TXT", ".txt", frozenset({"reflow"}),
                     "Só o texto, sem formatação.", "text/plain"),
    )
}

INPUTS = {
    "pdf": {"label": "PDF", "exts": [".pdf"], "layouts": ["fixed", "reflow"]},
    "epub": {"label": "EPUB", "exts": [".epub", ".kepub"], "layouts": ["fixed", "reflow"]},
    "comic": {"label": "Quadrinhos (CBZ/ZIP)", "exts": [".cbz", ".zip"], "layouts": ["fixed"]},
    "image": {"label": "Imagem", "exts": sorted(IMAGE_EXTS), "layouts": ["fixed"]},
    "mobi": {"label": "Kindle (MOBI/AZW3)", "exts": [".azw3", ".mobi", ".azw", ".prc", ".pdb"],
             "layouts": ["fixed", "reflow"]},
}

# Filtros do seletor de arquivos, como os do "Adicionar livros" do Calibre: um
# filtro com tudo o que o Prelo lê e um por família. Fonte única para a janela,
# o navegador e o arrastar e soltar.
BOOK_FILTERS: list[tuple[str, list[str]]] = [
    ("Livros", sorted({e for f in INPUTS.values() for e in f["exts"]})),
    ("Livros EPUB", [".epub", ".kepub"]),
    ("Livros para Kindle", [".azw3", ".mobi", ".azw", ".prc"]),
    ("Livros PDF", [".pdf"]),
    ("Quadrinhos", [".cbz", ".zip"]),
    ("Imagens", sorted(IMAGE_EXTS)),
]
BOOK_EXTENSIONS = frozenset(BOOK_FILTERS[0][1])


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "mimetype" in names:
                mt = zf.read("mimetype")[:64].strip()
                if mt == b"application/epub+zip":
                    return "epub"
            if any(n.lower().endswith(".opf") for n in names) and "META-INF/container.xml" in names:
                return "epub"
            if any(n.startswith("word/") for n in names):
                return "docx"
            if any(Path(n).suffix.lower() in IMAGE_EXTS for n in names):
                return "comic"
    except zipfile.BadZipFile:
        raise UnsupportedFormatError("O arquivo compactado está corrompido.") from None
    return "zip"


def detect_format(path: Path) -> str:
    """Devolve ``pdf``, ``epub``, ``comic``, ``image`` ou levanta erro claro."""
    path = Path(path)
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if not head:
        raise UnsupportedFormatError("O arquivo está vazio.")
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        kind = _zip_kind(path)
        if kind in ("epub", "comic"):
            return kind
        if kind == "docx":
            raise UnsupportedFormatError("DOCX ainda não é suportado como entrada.",
                                         hint="Salve como PDF no Word e converta o PDF.")
        raise UnsupportedFormatError("O ZIP não contém um livro nem imagens.")
    if head.startswith(b"Rar!"):
        raise UnsupportedFormatError("CBR (RAR) ainda não é suportado.",
                                     hint="Extraia as imagens e compacte em ZIP/CBZ.")
    if len(head) >= 68 and head[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        # PalmDOC: bytes 12-13 do registro 0 indicam criptografia (DRM).
        raise_if_mobi_drm(path)
        return "mobi"
    if len(head) >= 68 and head[60:64] in (b"PNRd", b"PDct", b"BVok"):
        raise UnsupportedFormatError("Este PDB não é um livro Kindle (é eReader ou outro formato Palm).",
                                     hint="Converta o original em EPUB ou PDF.")
    if head[:4] == b"TPZ0":
        raise UnsupportedFormatError("Livros Topaz (AZW1/TPZ) não são suportados.",
                                     hint="Baixe o livro de novo em AZW3, MOBI ou EPUB.")
    if head[:4] == b"CONT" or head[:8] == b"\xeaDRMION\xee":
        raise UnsupportedFormatError("KFX ainda não é suportado como entrada.",
                                     hint="Use a versão AZW3 ou MOBI do livro (no Kindle para PC: baixe pelo "
                                          "Gerenciar conteúdo como AZW3).")
    if (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n"
            or head[:4] == b"RIFF" and head[8:12] == b"WEBP"
            or head[:6] in (b"GIF87a", b"GIF89a") or head[:2] == b"BM"
            or head[:4] in (b"II*\x00", b"MM\x00*")):
        return "image"
    raise UnsupportedFormatError(f"Formato não reconhecido: {path.suffix or path.name}",
                                 hint="Formatos aceitos: PDF, EPUB, AZW3, MOBI, CBZ/ZIP e imagens.")


def raise_if_mobi_drm(path: Path) -> None:
    import struct

    with open(path, "rb") as fh:
        data = fh.read(78 + 8)
        if len(data) < 86:
            return
        offset = struct.unpack(">L", data[78:82])[0]
        fh.seek(offset)
        rec0 = fh.read(16)
    if len(rec0) >= 14 and struct.unpack(">H", rec0[12:14])[0] != 0:
        raise DrmError("Este livro Kindle tem DRM e não pode ser convertido.")


def check_compatible(input_format: str, output_format: str) -> None:
    if output_format not in OUTPUTS:
        raise UnsupportedFormatError(f"Formato de saída desconhecido: {output_format}",
                                     hint="Use epub, azw3, mobi, cbz, pdf ou txt.")
    in_layouts = set(INPUTS[input_format]["layouts"])
    if not in_layouts & OUTPUTS[output_format].layouts:
        raise IncompatibleConversionError(
            f"Não é possível gerar {OUTPUTS[output_format].label} a partir de "
            f"{INPUTS[input_format]['label']}: a entrada só tem páginas em imagem.",
            hint="Escolha EPUB, AZW3, MOBI, CBZ ou PDF.")
