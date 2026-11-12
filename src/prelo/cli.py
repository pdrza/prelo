"""Linha de comando.

    prelo convert livro.pdf -f azw3 --profile kindle-pw5 --temperature 5000
    prelo inspect livro.pdf
    prelo serve --port 8765
    prelo app

Cada opção do registro (``prelo options``) vira um parâmetro ``--nome-da-opcao``,
como no ``ebook-convert`` do Calibre: uma fonte única de verdade.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing
import os
import secrets
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .core.errors import PreloError
from .core.options import OPTIONS


def _add_option_args(parser: argparse.ArgumentParser) -> None:
    groups: dict[str, argparse._ArgumentGroup] = {}
    for spec in OPTIONS.values():
        group = groups.setdefault(spec.group, parser.add_argument_group(spec.group))
        kwargs: dict = {"dest": f"opt_{spec.name}", "default": None, "help": spec.help}
        if spec.kind == "bool":
            group.add_argument(spec.cli_flag, action=argparse.BooleanOptionalAction, **kwargs)
        else:
            if spec.kind == "choice":
                kwargs["choices"] = [str(c[0]) for c in spec.choices]
            group.add_argument(spec.cli_flag, metavar=spec.kind.upper(), **kwargs)


def _progress(stage: str, frac: float, msg: str) -> None:
    bar = "█" * int(frac * 24)
    sys.stderr.write(f"\r{frac:4.0%} {bar:<24} {msg[:48]:<48}")
    sys.stderr.flush()


@contextlib.contextmanager
def _stdout_only_for_paths():
    """--print-path: a saída padrão fica só com os caminhos gerados.

    Bibliotecas às vezes escrevem nela (o PyMuPDF, inclusive de dentro do código em C).
    Enquanto o bloco roda, tudo o que iria para a saída padrão vai para a de erro, e só
    a função entregue escreve na saída padrão de verdade.
    """
    sys.stdout.flush()
    real = os.dup(1)
    os.dup2(2, 1)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield lambda path: os.write(real, os.fsencode(str(path)) + b"\n")
    finally:
        sys.stderr.flush()
        os.dup2(real, 1)
        os.close(real)


def _print_result(res, quiet: bool) -> None:
    if not quiet and sys.stderr.isatty():
        sys.stderr.write("\n")
    for note in res.notes:
        print(f"ℹ {note}")
    for w in res.warnings:
        print(f"⚠ {w}")
    for n in res.option_notes:
        print(f"⚠ {n['message']}")
    print(f"✓ {res.output_path}  ({res.size / 1024:.0f} KB, {res.layout}, {res.seconds}s)")


def cmd_convert(args: argparse.Namespace) -> int:
    from .core.pipeline import ConversionRequest, convert

    options = {name[4:]: v for name, v in vars(args).items() if name.startswith("opt_") and v is not None}
    meta = {k: getattr(args, k) for k in ("title", "authors", "language") if getattr(args, k)}
    quiet = args.quiet or args.print_path
    code = 0
    with _stdout_only_for_paths() if args.print_path else contextlib.nullcontext() as emit_path:
        for src in args.inputs:
            try:
                res = convert(ConversionRequest(Path(src), args.format,
                                                output_dir=Path(args.output) if args.output else None,
                                                options=options, metadata=meta),
                              progress=None if quiet or not sys.stderr.isatty() else _progress,
                              log=(lambda m: sys.stderr.write(f"\n· {m}")) if args.verbose else None)
                if emit_path:  # para scripts: só o caminho gerado, uma linha por arquivo
                    for msg in [*res.notes, *res.warnings, *(n["message"] for n in res.option_notes)]:
                        print(f"{src}: {msg}", file=sys.stderr)
                    emit_path(res.output_path)
                else:
                    _print_result(res, quiet)
            except PreloError as exc:
                sys.stderr.write("\n")
                print(f"✗ {src}: {exc.message}" + (f"\n  {exc.hint}" if exc.hint else ""), file=sys.stderr)
                code = 1
    return code


def cmd_inspect(args: argparse.Namespace) -> int:
    from .core.library import inspect_file

    try:
        info, _cover = inspect_file(Path(args.file))
    except PreloError as exc:
        print(f"✗ {exc.message}", file=sys.stderr)
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.server import create_app

    token = None if args.no_token else (args.token or secrets.token_urlsafe(18))
    mode = "desktop" if args.local_paths else "server"
    app = create_app(token=token, mode=mode)
    host = args.host
    print(f"{APP_NAME} {__version__}: API em http://{host}:{args.port}/api/docs (modo {mode})")
    if token:
        print(f"Token: {token}   (cabeçalho X-Prelo-Token, ou abra http://{host}:{args.port}/?token={token})")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    from .app.desktop import main as desktop_main

    return desktop_main([*args.files, *(["--diagnostico"] if args.diagnostico else [])])


def cmd_list(args: argparse.Namespace) -> int:
    if args.what == "profiles":
        from .core.profiles import PROFILES

        for p in PROFILES:
            size = f"{p.width}×{p.height}" if p.width else "original"
            print(f"{p.id:20s} {size:>11s}  {'cor' if p.color else 'cinza':5s}  {p.name}")
    else:
        for spec in OPTIONS.values():
            print(f"{spec.cli_flag:28s} [{spec.group}] {spec.label} (padrão: {spec.default})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prelo", description=f"{APP_NAME}: conversor universal de livros")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="converte um ou mais livros")
    c.add_argument("inputs", nargs="+", help="arquivos de entrada (PDF, EPUB, AZW3/MOBI, CBZ, imagens)")
    c.add_argument("-f", "--format", required=True, choices=["epub", "azw3", "mobi", "cbz", "pdf", "txt"])
    c.add_argument("-o", "--output", help="pasta de saída (padrão: a pasta do original)")
    c.add_argument("--title", help="título")
    c.add_argument("--authors", help="autores, separados por vírgula")
    c.add_argument("--language", help="idioma (ex.: pt-BR)")
    c.add_argument("-q", "--quiet", action="store_true")
    c.add_argument("-v", "--verbose", action="store_true")
    c.add_argument("--print-path", action="store_true",
                   help="imprime só o caminho de cada arquivo gerado (para scripts); erros vão para stderr")
    _add_option_args(c)
    c.set_defaults(func=cmd_convert)

    i = sub.add_parser("inspect", help="mostra metadados e a análise do arquivo")
    i.add_argument("file")
    i.set_defaults(func=cmd_inspect)

    s = sub.add_parser("serve", help="sobe a API HTTP")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token")
    s.add_argument("--no-token", action="store_true", help="sem token (só para desenvolvimento)")
    s.add_argument("--local-paths", action="store_true",
                   help="aceita caminhos locais (modo desktop); só use em 127.0.0.1")
    s.set_defaults(func=cmd_serve)

    a = sub.add_parser("app", help="abre o aplicativo de desktop")
    a.add_argument("files", nargs="*", help="livros para colocar na estante ao abrir")
    a.add_argument("--diagnostico", action="store_true",
                   help="abre a janela, confere se a interface carregou, mostra um relatório e fecha")
    a.set_defaults(func=cmd_app)

    for what in ("profiles", "options"):
        p = sub.add_parser(what, help=f"lista {'os dispositivos' if what == 'profiles' else 'as opções'}")
        p.set_defaults(func=cmd_list, what=what)
    return parser


def _utf8_when_redirected() -> None:
    """Windows: redirecionada (pipe, arquivo), a saída usaria a página de código ANSI, que
    não tem ✓ nem ✗. Vai em UTF-8, como o caminho do --print-path. O console fica como está."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    _utf8_when_redirected()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
