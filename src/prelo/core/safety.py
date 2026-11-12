"""Como não quebrar arquivos.

Proteções herdadas do Calibre:

* trabalhar sempre sobre cópias (a entrada é copiada para uma pasta temporária);
* gravação atômica: o resultado é escrito num temporário **sem extensão** na
  mesma pasta e só então troca de lugar (nunca é confundido com um livro);
* saída vazia é erro;
* extração de ZIP segura (caminhos absolutos e ``..`` são recusados);
* recusar livros com DRM em vez de gerar um arquivo quebrado.

Proteções que o Calibre não tem:

* **nunca sobrescrever** um arquivo existente. No Calibre, converter MOBI → EPUB
  num livro que já tem EPUB substitui o EPUB sem cópia (risco "alto" da
  referência). Aqui o novo arquivo ganha um sufixo ``(2)``, ``(3)``...;
* limite contra "zip bomb" (taxa de compressão e tamanho total);
* nomes de arquivo que funcionam na memória dos leitores (Kindle e Kobo usam
  FAT32/exFAT, que recusam ``: ? * " < > |``) e em qualquer computador para onde
  o livro for copiado: sem caracteres proibidos, sem nomes reservados como
  ``CON`` e ``NUL`` e com caminhos curtos.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

from .errors import ConversionError, DrmError, UnsafeArchiveError
from .system import replace_file

RESERVED_NAMES = {  # nomes de dispositivo do DOS/Windows, recusados também em FAT32
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_NAME_CHARS = 120
MAX_PATH_CHARS = 250  # caminhos curtos: copiáveis para FAT32/exFAT e outros sistemas

# Limites contra zip bomb
MAX_UNCOMPRESSED_TOTAL = 4 * 1024**3
MAX_COMPRESSION_RATIO = 200
MAX_MEMBERS = 50_000

FONT_OBFUSCATION = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}


def sanitize_filename(name: str, *, fallback: str = "livro") -> str:
    """Nome de arquivo seguro no Linux, no Windows e na memória FAT32/exFAT dos leitores (sem a extensão)."""
    name = unicodedata.normalize("NFC", name or "")
    name = _INVALID_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if not name:
        name = fallback
    if name.split(".")[0].upper() in RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > MAX_NAME_CHARS:
        name = name[:MAX_NAME_CHARS].rstrip(" .")
    return name


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """Caminho que ainda não existe: ``nome.ext``, ``nome (2).ext``, ..."""
    ext = ext if ext.startswith(".") else f".{ext}"
    stem = sanitize_filename(stem)
    budget = MAX_PATH_CHARS - len(str(directory)) - len(ext) - 6
    if budget < 8:
        raise ConversionError("A pasta de saída tem um caminho longo demais.",
                              hint="Escolha uma pasta mais próxima da raiz do disco.")
    stem = stem[:budget].rstrip(" .") or "livro"
    candidate = directory / f"{stem}{ext}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){ext}"
        n += 1
    return candidate


class AtomicOutput:
    """Escreve num temporário sem extensão na mesma pasta e só troca no fim.

    Se algo falhar, nada é deixado para trás e nenhum arquivo existente é tocado.
    Depois do ``with``, ``final_path`` tem o caminho realmente gravado.

        writer = AtomicOutput(destino)
        with writer as tmp:
            gerar(tmp)
        print(writer.final_path)
    """

    def __init__(self, final_path: Path):
        self.final_path = Path(final_path)
        self.tmp: Path | None = None

    def __enter__(self) -> Path:
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".prelo-tmp-", dir=self.final_path.parent)
        os.close(fd)
        self.tmp = Path(tmp_name)
        return self.tmp

    def __exit__(self, exc_type, exc, tb) -> None:
        tmp = self.tmp
        try:
            if exc_type is None:
                if tmp is None or not tmp.exists() or tmp.stat().st_size == 0:
                    raise ConversionError("A conversão terminou sem gerar conteúdo (arquivo vazio).",
                                          hint="Veja o log da conversão para o motivo.")
                with open(tmp, "rb+") as fh:
                    os.fsync(fh.fileno())
                # mkstemp cria com 0600; o livro final segue as permissões normais (umask)
                umask = os.umask(0)
                os.umask(umask)
                os.chmod(tmp, 0o666 & ~umask)
                if self.final_path.exists():  # nunca sobrescrever (corrida rara)
                    self.final_path = unique_path(self.final_path.parent, self.final_path.stem,
                                                  self.final_path.suffix)
                replace_file(tmp, self.final_path)
        finally:
            if tmp is not None and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


@contextmanager
def atomic_output(final_path: Path) -> Iterator[Path]:
    with AtomicOutput(final_path) as tmp:
        yield tmp


class Workspace:
    """Pasta temporária de uma conversão. Removida no fim, salvo em depuração."""

    def __init__(self, keep: bool = False, prefix: str = "prelo-"):
        self.path = Path(tempfile.mkdtemp(prefix=prefix))
        self.keep = keep

    def sub(self, name: str) -> Path:
        p = self.path / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def copy_input(self, src: Path) -> Path:
        """Copia a entrada: o original nunca é aberto para escrita."""
        dest = self.sub("input") / f"source{src.suffix.lower()}"
        shutil.copy2(src, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IWUSR)
        return dest

    def cleanup(self) -> None:
        if not self.keep:
            shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


def _safe_member_path(name: str) -> PurePosixPath | None:
    name = name.replace("\\", "/")
    if not name or name.endswith("/"):
        return None
    pure = PurePosixPath(name)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", name):
        raise UnsafeArchiveError(f"O arquivo compactado tem um caminho absoluto: {name}")
    if any(part in ("..",) for part in pure.parts):
        raise UnsafeArchiveError(f"O arquivo compactado tenta gravar fora da pasta: {name}")
    parts = [p for p in pure.parts if p not in (".", "")]
    return PurePosixPath(*parts) if parts else None


def safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    """Extrai um ZIP recusando caminhos perigosos, links e zip bombs."""
    dest = dest.resolve()
    extracted: list[Path] = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise UnsafeArchiveError("O arquivo compactado está corrompido.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise UnsafeArchiveError("O arquivo compactado tem arquivos demais.")
        total = sum(i.file_size for i in infos)
        compressed = sum(i.compress_size for i in infos) or 1
        if total > MAX_UNCOMPRESSED_TOTAL or total / compressed > MAX_COMPRESSION_RATIO:
            raise UnsafeArchiveError("O arquivo compactado expande para um tamanho suspeito (zip bomb).")
        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise UnsafeArchiveError(f"O arquivo compactado contém um link simbólico: {info.filename}")
            rel = _safe_member_path(info.filename)
            if rel is None:
                continue
            target = (dest / Path(*rel.parts)).resolve()
            if dest not in target.parents:
                raise UnsafeArchiveError(f"Caminho fora da pasta: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            extracted.append(target)
    return extracted


def check_epub_drm(extracted_root: Path) -> None:
    """Recusa EPUB com criptografia que não seja só de fontes (regra do Calibre)."""
    meta = extracted_root / "META-INF"
    if (meta / "rights.xml").exists():
        raise DrmError("Este EPUB tem DRM (Adobe) e não pode ser convertido.",
                       hint="Use uma cópia sem proteção do livro.")
    enc = meta / "encryption.xml"
    if not enc.exists():
        return
    algorithms = set(re.findall(rb'Algorithm="([^"]+)"', enc.read_bytes()))
    algorithms = {a.decode("utf-8", "replace") for a in algorithms}
    # EncryptionMethod de conteúdo protegido; fontes ofuscadas são permitidas.
    if algorithms - FONT_OBFUSCATION:
        raise DrmError("Este EPUB tem DRM e não pode ser convertido.",
                       hint="Use uma cópia sem proteção do livro.")
