"""Fila de conversões.

Cada trabalho roda num processo próprio (``spawn``: o processo filho começa
limpo, sem herdar as threads do servidor). Mensagens de progresso e log voltam por uma fila. Se o processo
terminar sem mandar "concluído" ou "erro", o trabalho é marcado como falha:
nada fica "rodando para sempre".
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.errors import PreloError
from ..core.formats import OUTPUTS
from ..core.library import Library
from ..core.storage import Settings
from ..core.system import reveal_in_file_manager
from .worker import run_job


@dataclass
class Job:
    id: str
    book_id: str
    title: str
    output_format: str
    options: dict
    request: dict
    status: str = "queued"  # queued | running | done | error | cancelled
    stage: str = ""
    progress: float = 0.0
    message: str = "Na fila"
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: dict | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("request", None)
        return d


class JobManager:
    def __init__(self, library: Library, settings: Settings, *, from_network: bool = False):
        self.library, self.settings, self.from_network = library, settings, from_network
        self.ctx = mp.get_context("spawn")
        self.messages = self.ctx.Queue()
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.running: dict[str, tuple] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = False
        threading.Thread(target=self._dispatch_loop, name="prelo-dispatch", daemon=True).start()
        threading.Thread(target=self._message_loop, name="prelo-messages", daemon=True).start()

    # ------------------------------------------------------------ API
    def submit(self, book_id: str, output_format: str, options: dict | None = None,
               output_dir: str | None = None) -> Job:
        entry = self.library.get(book_id)
        if entry is None:
            raise KeyError(book_id)
        fmt = output_format.lower()
        if fmt not in OUTPUTS:
            raise PreloError(f"Formato de saída desconhecido: {output_format}")
        out_dir = output_dir or self.settings.data.get("output_dir") or None
        if self.from_network:
            out_dir = str(self.library.folder / "outputs")  # pela rede, nunca um caminho arbitrário
        request = {
            "input_path": entry.path, "output_format": fmt, "output_dir": out_dir,
            "options": dict(options or {}), "book_options": dict(entry.settings),
            "saved_defaults": dict(self.settings.data.get("saved_defaults") or {}),
            "metadata": dict(entry.metadata), "from_network": self.from_network,
        }
        # valida as opções já aqui, para o erro aparecer na hora e não no processo
        from ..core.options import OptionSet, Level

        OptionSet().apply(request["options"], Level.HIGH, "teste", from_network=self.from_network)
        job = Job(id=uuid.uuid4().hex[:12], book_id=book_id, title=entry.metadata.get("title") or entry.title,
                  output_format=fmt, options=request["options"], request=request)
        with self._lock:
            self.jobs[job.id] = job
        self._wake.set()
        return job

    def list(self) -> list[Job]:
        with self._lock:
            return list(reversed(self.jobs.values()))

    def get(self, job_id: str) -> Job:
        return self.jobs[job_id]

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self.jobs[job_id]
            if job.status == "queued":
                job.status, job.message, job.finished_at = "cancelled", "Cancelado", time.time()
            elif job.status == "running" and job_id in self.running:
                proc, event, _ = self.running[job_id]
                event.set()
                job.message = "Cancelando…"
                threading.Thread(target=self._force_stop, args=(job_id, proc), daemon=True).start()
        return job

    def remove(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job and job.status not in ("queued", "running"):
                del self.jobs[job_id]

    def shutdown(self) -> None:
        self._stop = True
        with self._lock:
            for proc, event, _ in self.running.values():
                event.set()
                proc.terminate()

    # ------------------------------------------------------- internos
    def _force_stop(self, job_id: str, proc) -> None:
        proc.join(8)
        if proc.is_alive():
            proc.terminate()
            proc.join(2)
        with self._lock:
            job = self.jobs.get(job_id)
            if job and job.status == "running":
                job.status, job.message, job.finished_at = "cancelled", "Cancelado", time.time()

    def _start(self, job: Job) -> None:
        event = self.ctx.Event()
        proc = self.ctx.Process(target=run_job, args=(job.id, job.request, self.messages, event),
                                name=f"prelo-job-{job.id}", daemon=False)
        proc.start()
        self.running[job.id] = (proc, event, None)
        job.status, job.started_at, job.message = "running", time.time(), "Iniciando"

    def _dispatch_loop(self) -> None:
        while not self._stop:
            self._wake.wait(0.5)
            self._wake.clear()
            with self._lock:
                for jid, (proc, event, dead_since) in list(self.running.items()):
                    job = self.jobs.get(jid)
                    if proc.is_alive():
                        continue
                    if job is None or job.status != "running":
                        del self.running[jid]
                        continue
                    # o processo terminou; espera as últimas mensagens chegarem
                    if dead_since is None:
                        self.running[jid] = (proc, event, time.time())
                    elif time.time() - dead_since > 2.0:
                        job.status = "error"
                        job.error = {"code": "processo_interrompido",
                                     "message": "O processo de conversão parou inesperadamente.",
                                     "hint": f"Código de saída {proc.exitcode}. Veja o log."}
                        job.message, job.finished_at = "Falhou", time.time()
                        del self.running[jid]
                limit = int(self.settings.data.get("parallel_jobs") or 1)
                for job in self.jobs.values():
                    if len(self.running) >= limit:
                        break
                    if job.status == "queued":
                        self._start(job)

    def _message_loop(self) -> None:
        while not self._stop:
            try:
                msg = self.messages.get(timeout=1)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                break
            kind, jid, *rest = msg
            with self._lock:
                job = self.jobs.get(jid)
                if job is None:
                    continue
                if kind == "progress":
                    job.stage, job.progress, job.message = rest[0], rest[1], rest[2]
                elif kind == "log":
                    job.log.append(rest[0])
                    del job.log[:-400]
                elif kind == "done":
                    job.status, job.result, job.progress = "done", rest[0], 1.0
                    job.message, job.finished_at = "Concluído", time.time()
                    self.library.record_conversion(job.book_id, rest[0])
                    if self.settings.data.get("open_folder_when_done") and not self.from_network:
                        try:
                            reveal_in_file_manager(rest[0]["output_path"])
                        except OSError:
                            pass
                elif kind == "error":
                    err = rest[0]
                    job.status = "cancelled" if err.get("code") == "cancelado" else "error"
                    job.error = None if job.status == "cancelled" else err
                    job.message = "Cancelado" if job.status == "cancelled" else err.get("message", "Falhou")
                    job.finished_at = time.time()
            self._wake.set()


def default_output_dir() -> str:
    return str(Path(os.path.expanduser("~")) / "Documents" / "Prelo")
