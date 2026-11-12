"""A documentação que sai do código (opções, dispositivos, formatos e rotas) está em dia,
e os links entre os documentos apontam para arquivos que existem."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_documentacao_gerada_em_dia():
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "gerar_docs.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_links_entre_documentos():
    quebrados = []
    for md in [RAIZ / "README.md", *(RAIZ / "docs").glob("*.md")]:
        for alvo in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", md.read_text(encoding="utf-8")):
            if "://" not in alvo and not (md.parent / alvo).is_file():
                quebrados.append(f"{md.relative_to(RAIZ)} → {alvo}")
    assert not quebrados, quebrados
