"""API HTTP do Prelo (FastAPI).

Dois modos:

* ``desktop``: o app de desktop sobe a API em 127.0.0.1 numa porta livre, com
  um token aleatório. A interface recebe o token por cookie. Caminhos locais
  são aceitos (o usuário escolhe arquivos e a pasta de saída).
* ``server``: para usar o Prelo como serviço. Caminhos locais e opções
  perigosas são recusados (só upload), como o servidor de conteúdo do Calibre
  faz com ``debug_pipeline`` e afins.

Documentação interativa: ``/api/docs``.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi import Path as Path_
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import APP_NAME, __version__
from ..core.errors import PreloError
from ..core.formats import INPUTS, OUTPUTS
from ..core.imaging import Adjustments
from ..core.library import Library
from ..core.options import OPTIONS
from ..core.preview import PreviewRenderer
from ..core.profiles import PROFILES
from ..core.reader import Reader
from ..core.storage import Settings, data_dir
from .jobs import JobManager, reveal_in_file_manager

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
TOKEN_COOKIE = "prelo_token"


class AddBooks(BaseModel):
    paths: list[str] = Field(..., description="Caminhos de arquivos locais (só no modo desktop).")


class PatchBook(BaseModel):
    metadata: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class NewJob(BaseModel):
    book_id: str
    output_format: str = Field(..., examples=["azw3"])
    options: dict[str, Any] = Field(default_factory=dict, examples=[{"profile": "kindle-pw5", "temperature": 5000}])
    output_dir: str | None = None


class ReadingPosition(BaseModel):
    page: int = Field(..., ge=1, description="Página aberta no leitor (contada de 1).")
    w: int = Field(600, ge=200, le=3000, description="Largura da página, como pedida ao leitor.")
    h: int = Field(800, ge=200, le=3000, description="Altura da página.")
    fs: int = Field(18, ge=10, le=48, description="Tamanho da letra.")


def page_adjustments(temperature: int = 6500, brightness: int = 0, contrast: int = 0, gamma: float = 1.0,
                     saturation: int = 0, invert: bool = False, grayscale: bool = False,
                     sharpen: bool = False, normalize: bool = False) -> Adjustments:
    """Ajustes de página na query (os mesmos da conversão), validados pelo registro de opções."""
    values = {"temperature": temperature, "brightness": brightness, "contrast": contrast, "gamma": gamma,
              "saturation": saturation, "invert": invert, "sharpen": sharpen, "normalize": normalize}
    for k, v in values.items():
        if k in OPTIONS:
            values[k] = OPTIONS[k].coerce(v)
    return Adjustments(**values, grayscale=grayscale)


def create_app(*, token: str | None = None, mode: Literal["desktop", "server"] = "desktop",
               folder: Path | None = None, on_quit=None, on_open=None) -> FastAPI:
    folder = folder or data_dir()
    settings = Settings(folder)
    library = Library(folder)
    preview = PreviewRenderer()
    reader = Reader(folder)
    jobs = JobManager(library, settings, from_network=(mode == "server"))
    local = mode == "desktop"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        jobs.shutdown()
        preview.close()
        reader.close()

    app = FastAPI(title=f"{APP_NAME} API", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None, lifespan=lifespan,
                  description="Conversor universal de livros (PDF, EPUB, AZW3, MOBI, CBZ) com layout fiel.")
    app.state.jobs, app.state.library, app.state.settings, app.state.reader = jobs, library, settings, reader

    # --------------------------------------------------------- segurança
    @app.middleware("http")
    async def check_token(request: Request, call_next):
        path = request.url.path
        if token and path.startswith("/api/") and path not in ("/api/health",):
            sent = request.headers.get("x-prelo-token") or request.cookies.get(TOKEN_COOKIE)
            if not sent or not secrets.compare_digest(sent, token):
                return JSONResponse({"code": "nao_autorizado", "message": "Token ausente ou inválido."}, 401)
        return await call_next(request)

    @app.exception_handler(PreloError)
    async def preloerror(_request: Request, exc: PreloError):
        return JSONResponse(exc.to_dict(), status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, exc: Exception):
        # O traceback completo vai para o log (prelo.log no app); a interface
        # mostra o tipo e a mensagem, em vez de um "Erro inesperado" mudo.
        import logging

        logging.getLogger("prelo.api").error("Erro inesperado", exc_info=exc)
        return JSONResponse({"code": "erro_interno", "message": "Erro interno na API.",
                             "hint": f"{type(exc).__name__}: {exc}"[:400]}, status_code=500)

    @app.exception_handler(KeyError)
    async def notfound(_request: Request, exc: KeyError):
        return JSONResponse({"code": "nao_encontrado", "message": f"Não encontrado: {exc.args[0]}"}, 404)

    def book_or_404(book_id: str):
        entry = library.get(book_id)
        if entry is None:
            raise HTTPException(404, "Livro não encontrado.")
        return entry

    def preview_file(entry) -> tuple[str, str]:
        """O arquivo que a prévia abre: um livro Kindle vira o EPUB do leitor, feito uma vez só."""
        if entry.format == "mobi":
            return str(reader.source(entry)), "epub"
        return entry.path, entry.format

    # ------------------------------------------------------------ geral
    @app.get("/api/health", tags=["geral"])
    def health() -> dict:
        from ..outputs.calibre_engine import calibre_executable

        return {"ok": True, "app": APP_NAME, "version": __version__, "mode": mode,
                "calibre": bool(calibre_executable()), "local_paths": local}

    @app.get("/api/formats", tags=["geral"])
    def formats() -> dict:
        return {"inputs": INPUTS, "outputs": [f.to_dict() for f in OUTPUTS.values()]}

    @app.get("/api/profiles", tags=["geral"])
    def profiles() -> list[dict]:
        return [p.to_dict() for p in PROFILES]

    @app.get("/api/options", tags=["geral"])
    def options() -> list[dict]:
        return [o.to_dict() for o in OPTIONS.values() if not (o.dangerous and not local)]

    # ----------------------------------------------------------- livros
    @app.get("/api/books", tags=["livros"])
    def list_books() -> list[dict]:
        return [b.to_dict() for b in library.list()]

    @app.post("/api/books", tags=["livros"])
    def add_books(body: AddBooks) -> dict:
        if not local:
            raise HTTPException(403, "Caminhos locais não são aceitos neste modo. Use /api/books/upload.")
        added, errors = [], []
        for p in body.paths:
            try:
                added.append(library.add(Path(p)).to_dict())
            except PreloError as exc:
                errors.append({"path": p, **exc.to_dict()})
        return {"added": added, "errors": errors}

    @app.post("/api/books/upload", tags=["livros"])
    def upload_books(files: list[UploadFile] = File(...)) -> dict:
        added, errors = [], []
        for f in files:
            suffix = Path(f.filename or "livro").suffix.lower()[:8]
            fd, tmp = tempfile.mkstemp(prefix="up-", suffix=suffix, dir=library.uploads)
            with open(fd, "wb") as out:
                shutil.copyfileobj(f.file, out, 1024 * 1024)
            tmp_path = Path(tmp)
            final = library.uploads / f"{Path(f.filename or 'livro').stem[:60]}-{tmp_path.stem[-6:]}{suffix}"
            tmp_path.rename(final)
            try:
                entry = library.add(final, uploaded=True)
                if Path(entry.path) != final.resolve():
                    final.unlink(missing_ok=True)  # já estava na estante
                added.append(entry.to_dict())
            except PreloError as exc:
                final.unlink(missing_ok=True)
                errors.append({"path": f.filename, **exc.to_dict()})
        return {"added": added, "errors": errors}

    @app.get("/api/books/{book_id}", tags=["livros"])
    def get_book(book_id: str) -> dict:
        return book_or_404(book_id).to_dict()

    @app.patch("/api/books/{book_id}", tags=["livros"])
    def patch_book(book_id: str, body: PatchBook) -> dict:
        book_or_404(book_id)
        return library.update(book_id, metadata=body.metadata, settings=body.settings).to_dict()

    @app.delete("/api/books/{book_id}", tags=["livros"])
    def delete_book(book_id: str) -> dict:
        book_or_404(book_id)
        preview.forget(book_id)  # fecha o PDF aberto pela prévia e pelo leitor antes de apagar
        reader.forget(book_id)
        library.remove(book_id)
        return {"ok": True}

    @app.get("/api/books/{book_id}/cover", tags=["livros"])
    def cover(book_id: str) -> Response:
        path = library.cover_path(book_id)
        if not path.exists():
            raise HTTPException(404, "Sem capa.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/books/{book_id}/pages", tags=["livros"])
    def pages(book_id: str) -> dict:
        b = book_or_404(book_id)
        return {"count": preview.page_count(b.id, *preview_file(b))}

    @app.get("/api/books/{book_id}/pages/{page}/preview", tags=["livros"],
             responses={200: {"content": {"image/jpeg": {}}}})
    def page_preview(book_id: str, page: int, w: int = Query(900, ge=120, le=1800), original: bool = False,
                     adj: Adjustments = Depends(page_adjustments)) -> Response:
        b = book_or_404(book_id)
        data = preview.render(b.id, *preview_file(b), page - 1, w, None if original else adj)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ leitor
    # w × h: a página na janela, em pixels CSS (num livro fixo, a caixa onde ela cabe);
    # fs: a letra (só livros fluidos); dpr: a densidade da tela.
    @app.get("/api/books/{book_id}/reader", tags=["leitor"])
    def reader_info(book_id: str, w: int = Query(600, ge=200, le=3000), h: int = Query(800, ge=200, le=3000),
                    fs: int = Query(18, ge=10, le=48)) -> dict:
        return reader.info(book_or_404(book_id), w, h, fs)

    @app.get("/api/books/{book_id}/reader/pages/{page}", tags=["leitor"],
             responses={200: {"content": {"image/jpeg": {}}}})
    def reader_page(book_id: str, page: int = Path_(..., ge=1), w: int = Query(600, ge=120, le=3000),
                    h: int = Query(800, ge=120, le=3000), fs: int = Query(18, ge=10, le=48),
                    dpr: float = Query(1.0, ge=0.5, le=3.0), adj: Adjustments = Depends(page_adjustments)) -> Response:
        data = reader.render(book_or_404(book_id), page, w, h, fs, dpr, adj)
        # a URL já diz tudo (página, paginação, tema): o navegador pode guardar
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/books/{book_id}/reader/pages/{page}/links", tags=["leitor"])
    def reader_links(book_id: str, page: int = Path_(..., ge=1), w: int = Query(600, ge=120, le=3000),
                     h: int = Query(800, ge=120, le=3000), fs: int = Query(18, ge=10, le=48)) -> list[dict]:
        return reader.links(book_or_404(book_id), page, w, h, fs)

    @app.put("/api/books/{book_id}/reader/position", tags=["leitor"])
    def reader_position(book_id: str, body: ReadingPosition) -> dict:
        b = book_or_404(book_id)
        state = reader.position(b, body.page, body.w, body.h, body.fs)
        library.update_reading(book_id, state)
        return state

    # --------------------------------------------------------- conversões
    @app.post("/api/jobs", tags=["conversões"])
    def new_job(body: NewJob) -> dict:
        book_or_404(body.book_id)
        if body.output_dir and not local:
            raise HTTPException(403, "A pasta de saída não pode ser definida pela rede.")
        return jobs.submit(body.book_id, body.output_format, body.options, body.output_dir).to_dict()

    @app.get("/api/jobs", tags=["conversões"])
    def list_jobs() -> list[dict]:
        return [j.to_dict() for j in jobs.list()]

    @app.get("/api/jobs/{job_id}", tags=["conversões"])
    def get_job(job_id: str) -> dict:
        return jobs.get(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/cancel", tags=["conversões"])
    def cancel_job(job_id: str) -> dict:
        return jobs.cancel(job_id).to_dict()

    @app.delete("/api/jobs/{job_id}", tags=["conversões"])
    def delete_job(job_id: str) -> dict:
        jobs.remove(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/download", tags=["conversões"])
    def download(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job.status != "done" or not job.result:
            raise HTTPException(409, "A conversão ainda não terminou.")
        path = Path(job.result["output_path"])
        if not path.exists():
            raise HTTPException(410, "O arquivo convertido não existe mais.")
        return FileResponse(path, filename=path.name, media_type=OUTPUTS[job.output_format].media_type)

    @app.post("/api/jobs/{job_id}/reveal", tags=["conversões"])
    def reveal(job_id: str) -> dict:
        if not local:
            raise HTTPException(403, "Disponível só no app de desktop.")
        job = jobs.get(job_id)
        if not job.result:
            raise HTTPException(409, "Nada para mostrar ainda.")
        reveal_in_file_manager(job.result["output_path"])
        return {"ok": True}

    @app.post("/api/desktop/open", tags=["geral"], include_in_schema=False)
    def desktop_open(body: AddBooks) -> dict:
        """Instância única: um segundo "prelo-app livro.pdf" entrega o arquivo aqui."""
        if not local or on_open is None:
            raise HTTPException(403, "Disponível só no app de desktop.")
        added, errors = [], []
        for p in body.paths:
            try:
                added.append(library.add(Path(p)).to_dict())
            except PreloError as exc:
                errors.append({"path": p, **exc.to_dict()})
        on_open(added, errors)
        return {"added": added, "errors": errors}

    @app.post("/api/quit", tags=["geral"])
    def quit_app() -> dict:
        """Encerra o app (só no modo desktop; usado quando a janela abre no navegador)."""
        if not local or on_quit is None:
            raise HTTPException(403, "Disponível só no app de desktop.")
        jobs.shutdown()
        on_quit()
        return {"ok": True}

    # ------------------------------------------------------- preferências
    @app.get("/api/settings", tags=["preferências"])
    def get_settings() -> dict:
        return settings.data

    @app.put("/api/settings", tags=["preferências"])
    def put_settings(body: dict[str, Any]) -> dict:
        if not local:
            body.pop("output_dir", None)
        return settings.update(body)

    # --------------------------------------------------------- interface
    @app.get("/", include_in_schema=False)
    def index(request: Request, token_q: str | None = Query(None, alias="token")) -> Response:
        if token and token_q:
            if not secrets.compare_digest(token_q, token):
                return JSONResponse({"code": "nao_autorizado", "message": "Token inválido."}, 401)
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict")
            return resp
        return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})

    if UI_DIR.exists():
        app.mount("/", StaticFiles(directory=UI_DIR), name="ui")
    return app
