"""Função que roda em um processo separado para cada conversão.

Regra do Calibre: a conversão roda fora do processo da interface. Se o
conversor travar ou cair (por exemplo, um PDF malformado derrubando o
MuPDF), a interface e a API continuam de pé e o trabalho é marcado com erro.
"""

from __future__ import annotations


def run_job(job_id: str, request: dict, messages, cancel_event) -> None:
    from pathlib import Path

    from ..core.errors import PreloError
    from ..core.pipeline import ConversionRequest, convert

    def progress(stage: str, frac: float, text: str) -> None:
        messages.put(("progress", job_id, stage, round(frac, 4), text))

    def log(text: str) -> None:
        messages.put(("log", job_id, text))

    try:
        req = ConversionRequest(**{**request, "input_path": Path(request["input_path"]),
                                   "output_dir": Path(request["output_dir"]) if request.get("output_dir") else None})
        result = convert(req, progress=progress, cancelled=cancel_event.is_set, log=log)
        messages.put(("done", job_id, result.to_dict()))
    except PreloError as exc:
        messages.put(("error", job_id, exc.to_dict()))
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira erro do trabalho, nunca da interface
        import traceback

        log(traceback.format_exc(limit=8))
        messages.put(("error", job_id, {"code": "erro_interno", "message": "Erro inesperado na conversão.",
                                        "hint": f"{type(exc).__name__}: {exc}"[:400]}))
