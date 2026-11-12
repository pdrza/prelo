"""Ajustes de página: temperatura, brilho, contraste, gama, modo noturno...

Tudo é aplicado com tabelas de consulta (``Image.point``), que rodam em C no
Pillow: uma página de 1236×1648 leva poucos milissegundos.

Também ficam aqui as rotinas que preparam a imagem final de uma página
(dividir página dupla, encaixar na tela, preencher com a cor do papel e
comprimir no formato mais adequado ao dispositivo).
"""

from __future__ import annotations

import io
import math
from dataclasses import asdict, dataclass

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

NEUTRAL_K = 6500


# ------------------------------------------------------------- temperatura
def kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """Cor aproximada de um corpo negro (algoritmo de Tanner Helland)."""
    t = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0
    clamp = lambda v: max(0.0, min(255.0, v))  # noqa: E731
    return clamp(r), clamp(g), clamp(b)


def white_point(kelvin: float) -> tuple[float, float, float]:
    """Fatores por canal que levam o branco neutro (6500 K) ao branco da
    temperatura pedida. O maior fator é 1, então a página nunca "estoura"."""
    if int(kelvin) == NEUTRAL_K:
        return (1.0, 1.0, 1.0)
    ref = kelvin_to_rgb(NEUTRAL_K)
    cur = kelvin_to_rgb(kelvin)
    factors = [c / r if r else 1.0 for c, r in zip(cur, ref)]
    top = max(factors)
    return tuple(f / top for f in factors)  # type: ignore[return-value]


@dataclass(frozen=True)
class Adjustments:
    temperature: int = NEUTRAL_K
    brightness: int = 0
    contrast: int = 0
    gamma: float = 1.0
    saturation: int = 0
    invert: bool = False
    grayscale: bool = False
    sharpen: bool = False
    normalize: bool = False

    @classmethod
    def from_options(cls, opts, *, grayscale: bool = False) -> "Adjustments":
        return cls(
            temperature=int(opts.temperature), brightness=int(opts.brightness),
            contrast=int(opts.contrast), gamma=float(opts.gamma),
            saturation=int(opts.saturation), invert=bool(opts.invert),
            grayscale=grayscale, sharpen=bool(opts.sharpen), normalize=bool(opts.normalize),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Adjustments":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def tone_is_identity(self) -> bool:
        return self.brightness == 0 and self.contrast == 0 and abs(self.gamma - 1.0) < 1e-6

    @property
    def is_identity(self) -> bool:
        return (self.tone_is_identity and self.temperature == NEUTRAL_K and self.saturation == 0
                and not (self.invert or self.grayscale or self.sharpen or self.normalize))


def _tone_curve(adj: Adjustments) -> list[float]:
    """Contraste em torno do cinza médio → brilho → gama (convenção do KCC:
    gama > 1 escurece os meios-tons, deixando o texto mais encorpado em e-ink)."""
    c = adj.contrast / 100.0
    k = 1.0 + c * 1.5 if c >= 0 else 1.0 + c
    b = adj.brightness / 100.0
    out = []
    for v in range(256):
        x = v / 255.0
        x = (x - 0.5) * k + 0.5
        x = x + (1.0 - x) * b if b >= 0 else x * (1.0 + b)
        x = min(1.0, max(0.0, x))
        if adj.gamma != 1.0:
            x = x ** adj.gamma
        out.append(x * 255.0)
    return out


def _invert_luminance(img: Image.Image) -> Image.Image:
    """Modo noturno que preserva o matiz: inverte só a luminância (Y de YCbCr).
    Uma inversão simples trocaria também as cores (vermelho viraria ciano)."""
    if img.mode == "L":
        return ImageOps.invert(img)
    y, cb, cr = img.convert("YCbCr").split()
    return Image.merge("YCbCr", (ImageOps.invert(y), cb, cr)).convert("RGB")


def flatten(img: Image.Image) -> Image.Image:
    """Garante RGB ou L. Transparência é composta sobre papel branco (converter
    RGBA direto para RGB deixaria as áreas transparentes pretas)."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    if img.mode in ("1", "I;16", "I"):
        return img.convert("L")
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img


def apply_adjustments(img: Image.Image, adj: Adjustments) -> Image.Image:
    img = flatten(img)
    if adj.is_identity:
        return img
    if adj.normalize:
        img = ImageOps.autocontrast(img, cutoff=0.5)
    if adj.invert:
        img = _invert_luminance(img)

    tint = adj.temperature != NEUTRAL_K and not adj.grayscale
    if tint and img.mode == "L":
        img = img.convert("RGB")  # página cinza em tela colorida: precisa de RGB para aquecer
    if not adj.tone_is_identity or tint:
        curve = _tone_curve(adj)
        if img.mode == "RGB":
            factors = white_point(adj.temperature) if tint else (1.0, 1.0, 1.0)
            lut: list[int] = []
            for f in factors:
                lut.extend(min(255, max(0, round(v * f))) for v in curve)
        else:
            lut = [min(255, max(0, round(v))) for v in curve]
        img = img.point(lut)
    if adj.saturation and img.mode == "RGB":
        img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 + adj.saturation / 100.0))
    if adj.grayscale and img.mode != "L":
        img = img.convert("L")
    if adj.sharpen:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=2))
    return img


# --------------------------------------------------------- página final
def _pixels(img: Image.Image):
    """Pixels da imagem (``getdata`` está obsoleto no Pillow 13+)."""
    getter = getattr(img, "get_flattened_data", None)
    return getter() if getter else img.getdata()


def border_color(img: Image.Image) -> int | tuple[int, int, int]:
    """Cor do papel: mediana dos pixels da borda da página."""
    small = img.copy()
    small.thumbnail((256, 256))
    w, h = small.size
    strips = [small.crop((0, 0, w, 2)), small.crop((0, h - 2, w, h)),
              small.crop((0, 0, 2, h)), small.crop((w - 2, 0, w, h))]
    if img.mode == "L":
        values = sorted(v for s in strips for v in _pixels(s))
        return values[len(values) // 2]
    chans = list(zip(*[px for s in strips for px in _pixels(s.convert("RGB"))]))
    return tuple(sorted(c)[len(c) // 2] for c in chans)  # type: ignore[return-value]


def content_bbox(img: Image.Image, threshold: int = 28) -> tuple[int, int, int, int] | None:
    """Área com conteúdo (tudo que difere da cor do papel), em pixels."""
    gray = img.convert("L")
    bg = border_color(gray)
    diff = ImageChops.difference(gray, Image.new("L", gray.size, bg))
    mask = diff.point(lambda v: 255 if v > threshold else 0)
    return mask.getbbox()


def split_spread(img: Image.Image, rtl: bool) -> list[Image.Image]:
    w, h = img.size
    if w <= h * 1.05:
        return [img]
    left, right = img.crop((0, 0, w // 2, h)), img.crop((w // 2, 0, w, h))
    return [right, left] if rtl else [left, right]


def fit_to_screen(img: Image.Image, size: tuple[int, int] | None, *, fill: bool) -> Image.Image:
    if not size:
        return img
    W, H = size
    w, h = img.size
    scale = min(W / w, H / h)
    if abs(scale - 1.0) > 0.002:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    if not fill or img.size == (W, H):
        return img
    canvas = Image.new(img.mode, (W, H), border_color(img))
    canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return canvas


_GRAY16 = [v for g in range(16) for v in (g * 17,) * 3]


def _png(img: Image.Image, *, levels: int | None = None) -> bytes:
    buf = io.BytesIO()
    if img.mode == "L" and levels == 16:
        # Telas e-ink mostram 16 tons de cinza: PNG de 4 bits, sem perda visível.
        idx = img.point(lambda v: (v + 8) // 17)
        pal = Image.frombytes("P", img.size, idx.tobytes())
        pal.putpalette(_GRAY16)
        pal.save(buf, "PNG", optimize=True, bits=4)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _jpeg(img: Image.Image, quality: int, *, subsampling: int = 2) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True, subsampling=subsampling, progressive=False)
    return buf.getvalue()


def encode_page(img: Image.Image, quality: str) -> tuple[bytes, str, str]:
    """Escolhe formato e compressão. Devolve ``(bytes, extensão, media type)``.

    Texto em tons de cinza comprime muito melhor (e sem borrar) em PNG de 16
    tons; fotos e páginas coloridas ficam menores em JPEG. Nos modos
    "equilibrada" e "compacta" as duas versões são geradas e fica a menor.
    """
    if img.mode == "L":
        if quality == "maxima":
            return _png(img), "png", "image/png"
        png16 = _png(img, levels=16)
        if quality == "alta":
            return png16, "png", "image/png"
        jpg = _jpeg(img, 85 if quality == "equilibrada" else 72)
        return (jpg, "jpg", "image/jpeg") if len(jpg) < len(png16) else (png16, "png", "image/png")
    if quality == "maxima":
        return _png(img), "png", "image/png"
    q = {"alta": 92, "equilibrada": 85, "compacta": 72}.get(quality, 88)
    jpg = _jpeg(img, q, subsampling=0 if quality == "alta" else 2)
    # Página de texto (poucas cores) fica menor e mais nítida em PNG com paleta;
    # foto continua menor em JPEG. Fica a menor das duas.
    colors = {"alta": 256, "equilibrada": 128, "compacta": 64}.get(quality, 128)
    pal = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    pal.save(buf, "PNG", optimize=True)
    png = buf.getvalue()
    if len(png) < len(jpg):
        return png, "png", "image/png"
    return jpg, "jpg", "image/jpeg"


def to_jpeg_bytes(data: bytes, quality: int = 90, max_side: int | None = None) -> bytes:
    """Converte qualquer imagem para JPEG (usado pelo MOBI 6 e pelas capas)."""
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        im = flatten(src)
        if im.mode == "P":
            im = im.convert("RGB")
        if max_side and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return _jpeg(im, quality)
