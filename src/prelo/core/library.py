"""A estante: livros adicionados ao Prelo, com capa, análise e histórico.

O identificador de cada livro é um hash do **conteúdo** (não do caminho).
Assim o mesmo arquivo adicionado duas vezes não duplica, e as configurações
salvas para o livro (nível HIGH, como no Calibre) sobrevivem se ele for
renomeado ou movido.
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from .errors import PreloError, UnsupportedFormatError
from .formats import comic_pages, detect_format
from .storage import read_json, write_json_atomic

COVER_HEIGHT = 720


@dataclass
class BookEntry:
    id: str
    path: str
    name: str
    format: str
    size: int
    title: str = ""
    authors: list[str] = field(default_factory=list)
    language: str = ""
    pages: int = 0
    added_at: float = 0.0
    uploaded: bool = False
    analysis: dict | None = None
    has_cover: bool = False
    cover_ratio: float = 1.5
    settings: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    conversions: list[dict] = field(default_factory=list)
    reading: dict = field(default_factory=dict)  # onde parou no leitor (ver core.reader)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["exists"] = Path(self.path).exists()
        return d


def content_id(path: Path) -> str:
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(1 << 20))
        if size > 2 << 20:
            fh.seek(-(1 << 20), 2)
            h.update(fh.read(1 << 20))
    return h.hexdigest()[:16]


def _cover_jpeg(img: Image.Image) -> tuple[bytes, float]:
    from .imaging import flatten

    img = flatten(img).convert("RGB")
    ratio = img.height / max(1, img.width)
    img.thumbnail((int(COVER_HEIGHT / max(ratio, 0.3)), COVER_HEIGHT), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86)
    return buf.getvalue(), ratio


def inspect_file(path: Path) -> tuple[dict, bytes | None]:
    """Metadados rápidos + capa (JPEG) para a estante."""
    fmt = detect_format(path)
    info: dict = {"format": fmt, "title": path.stem, "authors": [], "language": "", "pages": 0, "analysis": None}
    cover: bytes | None = None
    ratio = 1.5
    if fmt == "pdf":
        import pymupdf

        from ..inputs.pdf import analyze, open_pdf, read_metadata
        from .preview import MUPDF_LOCK

        with MUPDF_LOCK:  # a estante pode receber livros enquanto o leitor renderiza
            try:
                doc = open_pdf(path)
            except PreloError as exc:
                info["error"] = exc.to_dict()
                return info, None
            with doc:
                md = read_metadata(doc, path.name)
                info.update(title=md.title, authors=md.authors, language=md.language, pages=doc.page_count)
                info["analysis"] = analyze(doc, sample=24).to_dict()
                page = doc[0]
                scale = COVER_HEIGHT / max(1.0, page.rect.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                cover, ratio = _cover_jpeg(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    elif fmt == "epub":
        cover, ratio = _epub_info(path, info)
    elif fmt == "comic":
        with zipfile.ZipFile(path) as zf:
            imgs = comic_pages(zf.namelist())
            info["pages"] = len(imgs)
            if "ComicInfo.xml" in zf.namelist():
                m = re.search(rb"<Title>([^<]+)</Title>", zf.read("ComicInfo.xml"))
                if m:
                    info["title"] = m.group(1).decode("utf-8", "replace")
            if imgs:
                with Image.open(io.BytesIO(zf.read(imgs[0]))) as im:
                    im.load()
                    cover, ratio = _cover_jpeg(im)
    elif fmt == "mobi":
        from ..inputs.mobi import kindle_info

        data, raw_cover = kindle_info(path)
        info.update(data)
        if raw_cover:
            with Image.open(io.BytesIO(raw_cover)) as im:
                im.load()
                cover, ratio = _cover_jpeg(im)
    elif fmt == "image":
        info["pages"] = 1
        with Image.open(path) as im:
            im.load()
            cover, ratio = _cover_jpeg(im)
    info["cover_ratio"] = ratio
    return info, cover


def _epub_info(path: Path, info: dict) -> tuple[bytes | None, float]:
    from lxml import etree

    from ..inputs.epub import NS, XML_PARSER

    cover = None
    ratio = 1.5
    with zipfile.ZipFile(path) as zf:
        container = etree.fromstring(zf.read("META-INF/container.xml"), XML_PARSER)
        opf_path = container.find(".//c:rootfile", NS).get("full-path")
        opf = etree.fromstring(zf.read(opf_path), XML_PARSER)
        title = opf.xpath("string(.//dc:title)", namespaces=NS).strip()
        info["title"] = title or info["title"]
        info["authors"] = [a.strip() for a in opf.xpath(".//dc:creator/text()", namespaces=NS) if a.strip()]
        info["language"] = opf.xpath("string(.//dc:language)", namespaces=NS).strip()
        items = {i.get("id"): i for i in opf.iterfind(".//opf:manifest/opf:item", NS)}
        info["pages"] = len(opf.findall(".//opf:spine/opf:itemref", NS))
        cover_item = next((i for i in items.values() if "cover-image" in (i.get("properties") or "")), None)
        if cover_item is None:
            cid = opf.xpath("string(.//opf:meta[@name='cover']/@content)", namespaces=NS)
            cover_item = items.get(cid)
        if cover_item is not None and (cover_item.get("media-type") or "").startswith("image/"):
            base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            from urllib.parse import unquote

            try:
                data = zf.read(base + unquote(cover_item.get("href")))
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    cover, ratio = _cover_jpeg(im)
            except Exception:  # noqa: BLE001
                cover = None
    return cover, ratio


class Library:
    """A estante em ``library.json``; onde o leitor parou em cada livro, à parte, em
    ``leitura.json``, que muda a cada página virada e por isso é pequeno."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.covers = folder / "covers"
        self.uploads = folder / "uploads"
        self.covers.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.path = folder / "library.json"
        self.reading_path = folder / "leitura.json"
        self._lock = threading.RLock()
        self._reading_lock = threading.Lock()
        raw = read_json(self.path, {"books": []})
        self.books: dict[str, BookEntry] = {}
        for b in raw.get("books", []):
            try:
                entry = BookEntry(**{k: v for k, v in b.items() if k in BookEntry.__dataclass_fields__})
                self.books[entry.id] = entry
            except TypeError:
                continue
        saved = read_json(self.reading_path, None)
        if isinstance(saved, dict):
            for book_id, reading in saved.items():
                if book_id in self.books and isinstance(reading, dict):
                    self.books[book_id].reading = reading
        elif any(b.reading for b in self.books.values()):
            self._save_reading()  # a versão 0.2 guardava a posição dentro do library.json

    def save(self) -> None:
        with self._lock:
            books = []
            for b in self.books.values():
                data = asdict(b)
                del data["reading"]  # vai para o leitura.json
                books.append(data)
            write_json_atomic(self.path, {"books": books})

    def _save_reading(self) -> None:
        with self._reading_lock:
            write_json_atomic(self.reading_path,
                              {book_id: b.reading for book_id, b in list(self.books.items()) if b.reading})

    def list(self) -> list[BookEntry]:
        return sorted(self.books.values(), key=lambda b: b.added_at, reverse=True)

    def get(self, book_id: str) -> BookEntry | None:
        return self.books.get(book_id)

    def cover_path(self, book_id: str) -> Path:
        return self.covers / f"{book_id}.jpg"

    def add(self, path: Path, *, uploaded: bool = False) -> BookEntry:
        path = Path(path).resolve()
        if not path.is_file():
            raise UnsupportedFormatError(f"Arquivo não encontrado: {path.name}")
        book_id = content_id(path)
        with self._lock:
            existing = self.books.get(book_id)
            if existing is not None:
                if not Path(existing.path).exists():
                    existing.path = str(path)  # arquivo foi movido: atualiza
                    self.save()
                return existing
        try:
            info, cover = inspect_file(path)
        except PreloError:
            raise
        except Exception:  # noqa: BLE001 - análise falhou: o livro entra mesmo assim
            import traceback

            traceback.print_exc()
            info, cover = {"format": detect_format(path), "title": path.stem, "authors": [], "pages": 0}, None
        entry = BookEntry(id=book_id, path=str(path), name=path.name, format=info["format"],
                          size=path.stat().st_size, title=info.get("title", path.stem),
                          authors=info.get("authors", []), language=info.get("language", ""),
                          pages=info.get("pages", 0), added_at=time.time(), uploaded=uploaded,
                          analysis=info.get("analysis"), cover_ratio=info.get("cover_ratio", 1.5))
        if cover:
            self.cover_path(book_id).write_bytes(cover)
            entry.has_cover = True
        with self._lock:
            self.books[book_id] = entry
            self.save()
        return entry

    def update(self, book_id: str, *, metadata: dict | None = None, settings: dict | None = None) -> BookEntry:
        from .model import Metadata
        from .options import OPTIONS, unknown_option

        with self._lock:
            entry = self.books[book_id]
            if metadata is not None:
                entry.metadata = {k: v for k, v in metadata.items() if k in Metadata.EDITABLE and v not in (None, "")}
            if settings is not None:
                clean = {}
                for name, v in settings.items():
                    if name not in OPTIONS:
                        raise unknown_option(name)
                    clean[name] = OPTIONS[name].coerce(v)
                entry.settings = clean
            self.save()
            return entry

    def update_reading(self, book_id: str, reading: dict) -> BookEntry:
        entry = self.books[book_id]
        entry.reading = dict(reading)
        self._save_reading()
        return entry

    def record_conversion(self, book_id: str, result: dict) -> None:
        with self._lock:
            entry = self.books.get(book_id)
            if entry is None:
                return
            entry.conversions.insert(0, {k: result.get(k) for k in
                                         ("output_path", "output_format", "layout", "size", "seconds")}
                                     | {"at": time.time()})
            del entry.conversions[30:]
            self.save()

    def remove(self, book_id: str) -> None:
        with self._lock:
            entry = self.books.pop(book_id, None)
            if entry is None:
                return
            for path in (self.cover_path(book_id), Path(entry.path) if entry.uploaded else None):
                if path is None:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:  # sem permissão ou em uso: fica para a próxima limpeza
                    pass
            self.save()
        if entry.reading:
            self._save_reading()
