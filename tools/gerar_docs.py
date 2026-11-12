"""Gera as partes da documentação que saem do código e confere se estão em dia.

    python tools/gerar_docs.py           # regrava os arquivos em docs/
    python tools/gerar_docs.py --check   # só confere (tests/test_docs.py roda isso)

Fonte única de verdade: as opções vêm de ``core/options.py``, os dispositivos de
``core/profiles.py``, os formatos de ``core/formats.py`` e as rotas da própria
API. Arquivos inteiros (``opcoes.md``, ``dispositivos.md``) são gerados; nos
outros, só o trecho entre ``<!-- gerado:NOME -->`` e ``<!-- /gerado:NOME -->``.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from prelo.core.formats import BOOK_FILTERS, INPUTS, OUTPUTS, check_compatible  # noqa: E402
from prelo.core.options import OPTIONS  # noqa: E402
from prelo.core.profiles import DEFAULT_FOR_FORMAT, PROFILES  # noqa: E402

AVISO = "<!-- Arquivo gerado por tools/gerar_docs.py a partir do código. Não edite à mão. -->"
GRUPOS = ["Conversão", "Página", "Texto", "Kindle", "PDF", "Avançado"]
TIPOS = {"bool": "sim/não", "int": "inteiro", "float": "decimal", "choice": "escolha", "str": "texto"}

# Rotas: descrição curta de cada uma. Rota nova sem descrição faz o gerador falhar,
# para nenhuma ficar sem documentação.
ROTAS = {
    ("GET", "/api/health"): "Estado da API: versão, modo (`desktop`/`server`), se o Calibre foi encontrado e se aceita caminhos locais. Não exige token.",
    ("GET", "/api/formats"): "Formatos de entrada (com extensões e modos) e de saída.",
    ("GET", "/api/profiles"): "Dispositivos, com resolução, cor/e-ink e formato preferido.",
    ("GET", "/api/options"): "Todas as opções de conversão, com tipo, padrão, faixa e ajuda. No modo servidor, as perigosas não aparecem.",
    ("GET", "/api/books"): "Livros da estante, do mais recente para o mais antigo.",
    ("POST", "/api/books"): "Adiciona arquivos pelo caminho (`{\"paths\": [...]}`). **Só no modo desktop.**",
    ("POST", "/api/books/upload"): "Envia arquivos (multipart, campo `files`) e os coloca na estante.",
    ("GET", "/api/books/{book_id}"): "Um livro da estante.",
    ("PATCH", "/api/books/{book_id}"): "Salva metadados editados (`metadata`) e/ou opções do livro (`settings`, nível HIGH).",
    ("DELETE", "/api/books/{book_id}"): "Tira o livro da estante. O arquivo original não é apagado (só a cópia enviada por upload).",
    ("GET", "/api/books/{book_id}/cover"): "Capa em JPEG.",
    ("GET", "/api/books/{book_id}/pages"): "Número de páginas que a prévia consegue mostrar.",
    ("GET", "/api/books/{book_id}/pages/{page}/preview"): "Página `page` (a partir de 1) em JPEG, com os ajustes da query: `w`, `original`, `temperature`, `brightness`, `contrast`, `gamma`, `saturation`, `invert`, `grayscale`, `sharpen`, `normalize`.",
    ("GET", "/api/books/{book_id}/reader"): "Leitor: abre o livro paginado para uma página de `w` × `h` pixels e letra `fs` (livros fluidos). Devolve o tipo (`reflow` ou `fixed`), o número de páginas, o sumário, a proporção de cada página (livros fixos) e a página onde o leitor parou.",
    ("GET", "/api/books/{book_id}/reader/pages/{page}"): "Página `page` do leitor em JPEG, na paginação de `w`, `h` e `fs`, com densidade `dpr` e os mesmos ajustes de página da prévia (o tema do leitor). Num livro fixo, `w` × `h` é a caixa onde a página cabe.",
    ("GET", "/api/books/{book_id}/reader/pages/{page}/links"): "Links internos da página (sumário do livro, notas): área em frações da página e a página de destino.",
    ("PUT", "/api/books/{book_id}/reader/position"): "Guarda onde o leitor parou (`page`, `w`, `h`, `fs`). Num livro fluido, a posição é o capítulo e as letras antes da página, e sobrevive a outra letra e a reabrir o app.",
    ("POST", "/api/jobs"): "Cria uma conversão: `book_id`, `output_format`, `options` e, só no modo desktop, `output_dir`.",
    ("GET", "/api/jobs"): "Conversões desta sessão, da mais nova para a mais antiga.",
    ("GET", "/api/jobs/{job_id}"): "Uma conversão: estado, etapa, progresso, log, resultado ou erro.",
    ("POST", "/api/jobs/{job_id}/cancel"): "Cancela (na fila: na hora; rodando: pede para parar e encerra o processo se preciso).",
    ("DELETE", "/api/jobs/{job_id}"): "Tira da lista uma conversão que já terminou.",
    ("GET", "/api/jobs/{job_id}/download"): "Baixa o arquivo convertido (409 se ainda não terminou, 410 se o arquivo sumiu).",
    ("POST", "/api/jobs/{job_id}/reveal"): "Mostra o arquivo convertido no gerenciador de arquivos. **Só no modo desktop.**",
    ("POST", "/api/quit"): "Encerra o app (o mesmo que Prelo › Sair). **Só no modo desktop.**",
    ("GET", "/api/settings"): "Preferências.",
    ("PUT", "/api/settings"): "Altera preferências (só as chaves conhecidas; pela rede, `output_dir` é ignorado).",
}


def _valor(spec, value) -> str:
    if spec.kind == "bool":
        return "sim" if value else "não"
    if value in ("", None):
        return "(vazio)"
    return f"`{value}`"


def _faixa(spec) -> str:
    if spec.kind == "choice":
        return "<br>".join(f"`{v}` {label}" for v, label in spec.choices)
    if spec.kind in ("int", "float") and spec.minimum is not None:
        return f"{spec.minimum:g} a {spec.maximum:g}"
    if spec.kind == "bool":
        return "sim ou não"
    return "texto livre"


def _flag(spec) -> str:
    if spec.kind == "bool":
        return f"`{spec.cli_flag}`<br>`--no-{spec.cli_flag[2:]}`"
    return f"`{spec.cli_flag} {spec.kind.upper()}`"


def opcoes_md() -> str:
    out = [AVISO, "", "# Opções de conversão", "",
           f"O Prelo tem {len(OPTIONS)} opções. Cada uma tem o mesmo nome na interface, na linha de comando "
           "(`--nome-da-opcao`), na API (`\"options\": {\"nome_da_opcao\": valor}`) e nas configurações salvas. "
           "Nome desconhecido é **erro**, com sugestão do nome mais parecido; valor fora da faixa também.", "",
           "Como uma opção chega ao valor final (níveis, travas e de onde veio cada valor) está em "
           "[conversao.md](conversao.md#prioridade-das-opções).", ""]
    for group in GRUPOS:
        specs = [s for s in OPTIONS.values() if s.group == group]
        if not specs:
            continue
        out += [f"## {group}", "", "| Opção | Linha de comando | Tipo | Padrão | Valores | O que faz |",
                "|---|---|---|---|---|---|"]
        for s in specs:
            extra = []
            if s.dangerous:
                extra.append("**Recusada quando chega pela rede** (modo servidor).")
            if s.secret:
                extra.append("Não aparece nos relatórios (vira `•••`).")
            desc = " ".join([s.help, *extra]).replace("|", "\\|")
            out.append(f"| **{s.label}**<br>`{s.name}` | {_flag(s)} | {TIPOS[s.kind]} | {_valor(s, s.default)} "
                       f"| {_faixa(s)} | {desc} |")
        out.append("")
    missing = sorted({s.group for s in OPTIONS.values()} - set(GRUPOS))
    if missing:
        raise SystemExit(f"grupos de opção sem seção na documentação: {missing}")
    return "\n".join(out)


def dispositivos_md() -> str:
    out = [AVISO, "", "# Dispositivos", "",
           "No layout fiel, cada página é renderizada **na resolução exata da tela** escolhida: o leitor não "
           "precisa reescalar nada, então fica nítido sem inflar o arquivo. O dispositivo também decide as "
           "cores: telas e-ink monocromáticas recomendam tons de cinza (nível MED, então a sua escolha vale "
           "mais que a recomendação).", "",
           "| Id | Aparelho | Família | Resolução | Tela | Formato preferido |", "|---|---|---|---|---|---|"]
    for p in PROFILES:
        size = f"{p.width} × {p.height}" if p.width else "tamanho do original"
        tela = ("e-ink " if p.eink else "") + ("colorida" if p.color else "monocromática")
        out.append(f"| `{p.id}` | {p.name} | {p.family} | {size} | {tela} | {p.preferred_format.upper()} |")
    out += ["", "## Dispositivo automático", "",
            "Com o dispositivo em **Automático** (`--profile auto`, o padrão), o Prelo escolhe pelo formato de saída:", "",
            "| Saída | Dispositivo usado |", "|---|---|"]
    names = {p.id: p.name for p in PROFILES}
    for fmt, pid in DEFAULT_FOR_FORMAT.items():
        out.append(f"| {OUTPUTS[fmt].label} | `{pid}` ({names[pid]}) |")
    out += ["", "No perfil `original` não há tela de destino: as páginas são renderizadas pela opção "
            "**Resolução (DPI)** (`--dpi`, 0 = 200 DPI). Em qualquer perfil, nenhum lado de página passa de "
            "4096 px.", "",
            "O dispositivo padrão é escolhido na janela **Dispositivos** (vale para a janela Converter) e pode "
            "ser trocado em cada conversão.", ""]
    return "\n".join(out)


def formatos_bloco() -> str:
    out = ["### Entrada", "", "| Formato | Extensões | Modos possíveis |", "|---|---|---|"]
    modos = {"fixed": "layout fiel", "reflow": "texto fluido"}
    for f in INPUTS.values():
        out.append(f"| {f['label']} | {', '.join(f'`{e}`' for e in f['exts'])} | "
                   f"{', '.join(modos[m] for m in f['layouts'])} |")
    out += ["", "### Saída", "", "| Formato | Extensão | Modos | Para quê |", "|---|---|---|---|"]
    for o in OUTPUTS.values():
        out.append(f"| {o.label} | `{o.ext}` | {', '.join(modos[m] for m in sorted(o.layouts))} | {o.description} |")
    out += ["", "### O que combina com o quê", "",
            "| Entrada \\ Saída | " + " | ".join(o.label for o in OUTPUTS.values()) + " |",
            "|---|" + "---|" * len(OUTPUTS)]
    for key, f in INPUTS.items():
        cells = []
        for fmt in OUTPUTS:
            try:
                check_compatible(key, fmt)
                cells.append("sim")
            except Exception:  # noqa: BLE001
                cells.append("não")
        out.append(f"| {f['label']} | " + " | ".join(cells) + " |")
    out += ["", "### Filtros do seletor de arquivos", "", "| Filtro | Extensões |", "|---|---|"]
    for name, exts in BOOK_FILTERS:
        out.append(f"| {name} | {' '.join(f'`{e}`' for e in exts)} |")
    return "\n".join(out)


def rotas_bloco() -> str:
    from fastapi.routing import APIRoute

    from prelo.api.server import create_app

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(token="x", folder=Path(tmp))
        rotas = []
        for r in app.routes:
            if not isinstance(r, APIRoute) or not r.path.startswith("/api/") or not r.include_in_schema:
                continue
            for m in sorted(r.methods - {"HEAD"}):
                rotas.append((m, r.path, (r.tags or [""])[0]))
        app.state.jobs.shutdown()
    faltando = [f"{m} {p}" for m, p, _ in rotas if (m, p) not in ROTAS]
    if faltando:
        raise SystemExit("rotas sem descrição em tools/gerar_docs.py: " + ", ".join(faltando))
    out = ["| Método | Rota | Grupo | O que faz |", "|---|---|---|---|"]
    for m, p, tag in rotas:
        out.append(f"| `{m}` | `{p}` | {tag} | {ROTAS[(m, p)]} |")
    return "\n".join(out)


def _substitui(texto: str, nome: str, bloco: str, arquivo: str) -> str:
    # o trecho entre os marcadores pode estar vazio (marcadores em linhas seguidas)
    padrao = re.compile(rf"(<!-- gerado:{nome} -->\n)(?:.*?\n)?(<!-- /gerado:{nome} -->)", re.DOTALL)
    if not padrao.search(texto):
        raise SystemExit(f"{arquivo}: faltam os marcadores <!-- gerado:{nome} --> e <!-- /gerado:{nome} -->")
    return padrao.sub(lambda m: m.group(1) + bloco + "\n" + m.group(2), texto)


def alvos() -> dict[Path, str]:
    docs = RAIZ / "docs"
    out = {docs / "opcoes.md": opcoes_md() + "\n", docs / "dispositivos.md": dispositivos_md() + "\n"}
    for arquivo, nome, bloco in (("formatos.md", "formatos", formatos_bloco()), ("api.md", "rotas", rotas_bloco())):
        path = docs / arquivo
        out[path] = _substitui(path.read_text(encoding="utf-8"), nome, bloco, arquivo)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="só confere; falha se algo estiver desatualizado")
    args = ap.parse_args(argv)
    desatualizados = []
    for path, conteudo in alvos().items():
        atual = path.read_text(encoding="utf-8") if path.exists() else ""
        if atual == conteudo:
            continue
        if args.check:
            desatualizados.append(path.relative_to(RAIZ).as_posix())
        else:
            path.write_text(conteudo, encoding="utf-8", newline="\n")
            print(f"atualizado: {path.relative_to(RAIZ)}")
    if desatualizados:
        print("documentação desatualizada (rode python tools/gerar_docs.py): " + ", ".join(desatualizados))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
