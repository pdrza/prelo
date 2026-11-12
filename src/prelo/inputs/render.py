"""Renderização de páginas (layout fiel), em paralelo.

Cada processo abre o documento por conta própria (objetos do MuPDF não podem
ser compartilhados entre processos) e cuida de um bloco de páginas
consecutivas. As imagens vão direto para o disco da pasta de trabalho.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image

from ..core.errors import CancelledError
from ..core.imaging import Adjustments, apply_adjustments, encode_page, fit_to_screen, split_spread

MAX_SIDE = 4096  # leitores costumam limitar imagens a ~4000 px


@dataclass
class RenderConfig:
    source: str
    out_dir: str
    target: tuple[int, int] | None
    dpi: int
    grayscale: bool
    adjustments: dict
    quality: str
    fill_screen: bool
    split_spreads: bool
    rtl: bool
    password: str = ""
    filetype: str | None = None
    crop_boxes: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)
    # Para EPUB paginado pelo MuPDF: (largura, altura, tamanho da fonte) em pontos
    reflow_layout: tuple[float, float, float] | None = None


@dataclass
class RenderedPage:
    index: int
    part: int
    parts: int
    filename: str
    media_type: str
    width: int
    height: int
    source_size: tuple[float, float]
    label: str


def open_document(cfg: RenderConfig) -> pymupdf.Document:
    doc = pymupdf.open(cfg.source, filetype=cfg.filetype) if cfg.filetype else pymupdf.open(cfg.source)
    if doc.needs_pass and cfg.password:
        doc.authenticate(cfg.password)
    if cfg.reflow_layout:
        w, h, fs = cfg.reflow_layout
        doc.layout(width=w, height=h, fontsize=fs)
    return doc


def _scale_for(area: pymupdf.Rect, cfg: RenderConfig) -> float:
    if cfg.target:
        W, H = cfg.target
        width = area.width / 2 if (cfg.split_spreads and area.width > area.height * 1.05) else area.width
        scale = min(W / width, H / area.height)
    else:
        scale = (cfg.dpi or 200) / 72.0
    return min(scale, MAX_SIDE / max(area.width, area.height))


def render_pages(cfg: RenderConfig, indices: list[int]) -> list[RenderedPage]:
    results: list[RenderedPage] = []
    doc = open_document(cfg)
    try:
        for i in indices:
            page = doc[i]
            rect = page.rect
            crop = cfg.crop_boxes.get(i)
            clip = pymupdf.Rect(crop) & rect if crop else None
            area = clip if clip and not clip.is_empty else rect
            scale = _scale_for(area, cfg)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip if clip else None,
                                  colorspace=pymupdf.csGRAY if cfg.grayscale else pymupdf.csRGB,
                                  alpha=False, annots=True)
            img = Image.frombytes("L" if pix.n == 1 else "RGB", (pix.width, pix.height), pix.samples)
            del pix
            try:
                label = page.get_label() or str(i + 1)
            except Exception:  # noqa: BLE001 - rótulos malformados não podem derrubar a conversão
                label = str(i + 1)
            results.extend(finalize_image(img, cfg, i, label, (rect.width, rect.height), f"p{i + 1:04d}"))
    finally:
        doc.close()
    return results


def finalize_image(img: Image.Image, cfg: RenderConfig, index: int, label: str,
                   source_size: tuple[float, float], stem: str) -> list[RenderedPage]:
    """Divide, ajusta, encaixa na tela e grava uma imagem de página."""
    adj = Adjustments.from_dict({**cfg.adjustments, "grayscale": cfg.grayscale})
    if cfg.grayscale and img.mode not in ("L",):
        img = img.convert("L") if img.mode in ("RGB", "L") else img
    parts = split_spread(img, cfg.rtl) if cfg.split_spreads else [img]
    out: list[RenderedPage] = []
    for k, part in enumerate(parts):
        part = apply_adjustments(part, adj)
        part = fit_to_screen(part, cfg.target, fill=cfg.fill_screen)
        data, ext, media_type = encode_page(part, cfg.quality)
        suffix = "abcdefgh"[k] if len(parts) > 1 else ""
        name = f"{stem}{suffix}.{ext}"
        (Path(cfg.out_dir) / name).write_bytes(data)
        out.append(RenderedPage(index, k, len(parts), name, media_type, part.width, part.height,
                                source_size, label))
    return out


def process_images(cfg: RenderConfig, items: list[tuple[int, str, str]]) -> list[RenderedPage]:
    """Páginas que já são imagens (CBZ, pasta de imagens): ``(índice, caminho, rótulo)``."""
    from ..core.imaging import flatten

    out: list[RenderedPage] = []
    for index, path, label in items:
        with Image.open(path) as src:
            src.load()
            img = flatten(src)
        if cfg.target and not cfg.split_spreads:
            # reduz antes dos ajustes: mais rápido e o resultado é o mesmo
            W, H = cfg.target
            if img.width > W * 1.5 and img.height > H * 1.5:
                img.thumbnail((W * 2, H * 2), Image.Resampling.LANCZOS)
        out.extend(finalize_image(img, cfg, index, label, (float(img.width), float(img.height)),
                                  f"p{index + 1:04d}"))
    return out


def _chunks(indices: list, n: int) -> list[list]:
    size = max(1, -(-len(indices) // n))
    return [indices[i:i + size] for i in range(0, len(indices), size)]


def worker_count(requested: int, pages: int) -> int:
    if requested:
        return max(1, min(requested, pages))
    cpus = os.cpu_count() or 2
    return max(1, min(cpus - 1 if cpus > 2 else cpus, 8, pages))


def render_all(cfg: RenderConfig, indices: list, *, workers: int = 0,
               progress: Callable[[float], None] | None = None,
               cancelled: Callable[[], bool] | None = None,
               fn: Callable = render_pages) -> list[RenderedPage]:
    """Processa as páginas em paralelo e devolve na ordem original.

    ``fn`` é :func:`render_pages` (índices de página) ou :func:`process_images`
    (tuplas ``(índice, caminho, rótulo)``)."""
    Path(cfg.out_dir).mkdir(parents=True, exist_ok=True)
    n = worker_count(workers, len(indices))
    done: list[RenderedPage] = []
    if n == 1 or len(indices) <= 6:
        finished = 0
        for chunk in _chunks(indices, max(1, len(indices) // 4)):
            if cancelled and cancelled():
                raise CancelledError("Conversão cancelada.")
            done.extend(fn(cfg, chunk))
            finished += len(chunk)
            if progress:
                progress(finished / len(indices))
    else:
        chunks = _chunks(indices, n * 4)
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as ex:
            pending = {ex.submit(fn, cfg, c): len(c) for c in chunks}
            finished = 0
            while pending:
                ready, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                if cancelled and cancelled():
                    ex.shutdown(wait=False, cancel_futures=True)
                    raise CancelledError("Conversão cancelada.")
                for fut in ready:
                    finished += pending.pop(fut)
                    done.extend(fut.result())
                    if progress:
                        progress(finished / len(indices))
    done.sort(key=lambda p: (p.index, p.part))
    return done
