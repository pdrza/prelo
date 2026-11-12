"""Gera o ícone do Windows (``ui/img/icon.ico``) a partir do ``ui/img/icon.svg``.

    python tools/gerar_icone.py           # regrava o icon.ico
    python tools/gerar_icone.py --check   # só confere se está em dia com o SVG

Cada tamanho é desenhado direto do SVG (pelo MuPDF), em vez de reduzido de uma
imagem grande: os cantos e as folhas ficam nítidos também em 16 e 24 px, que são
os da barra de título, do Explorador de Arquivos e do menu "Abrir com".
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
IMG = RAIZ / "src" / "prelo" / "ui" / "img"
TAMANHOS = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def desenhar(svg: Path, lado: int) -> Image.Image:
    with pymupdf.open(svg) as doc:
        page = doc[0]
        escala = lado / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(escala, escala), alpha=True)
        img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    return img if img.size == (lado, lado) else img.resize((lado, lado), Image.Resampling.LANCZOS)


def gerar_ico(svg: Path) -> bytes:
    imagens = [desenhar(svg, t) for t in TAMANHOS]
    buf = io.BytesIO()
    imagens[-1].save(buf, "ICO", sizes=[(t, t) for t in TAMANHOS], append_images=imagens[:-1])
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="só confere, sem gravar")
    args = parser.parse_args()
    dados = gerar_ico(IMG / "icon.svg")
    destino = IMG / "icon.ico"
    if args.check:
        if not destino.exists() or destino.read_bytes() != dados:
            print("icon.ico desatualizado (rode python tools/gerar_icone.py)")
            return 1
        return 0
    destino.write_bytes(dados)
    with Image.open(destino) as ico:
        print(f"{destino.relative_to(RAIZ)}: {sorted(ico.info['sizes'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
