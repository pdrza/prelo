"""Erros do Prelo.

Cada erro tem um ``code`` estável (usado pela API e pela interface) e uma
mensagem em português pronta para mostrar ao usuário. ``hint`` sugere o que
fazer em seguida.
"""

from __future__ import annotations


class PreloError(Exception):
    code = "erro"

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class UnsupportedFormatError(PreloError):
    code = "formato_nao_suportado"


class IncompatibleConversionError(PreloError):
    code = "conversao_incompativel"


class DrmError(PreloError):
    code = "drm"


class PasswordRequiredError(PreloError):
    code = "senha_necessaria"


class UnknownOptionError(PreloError):
    code = "opcao_desconhecida"


class InvalidOptionError(PreloError):
    code = "opcao_invalida"


class UnsafeArchiveError(PreloError):
    code = "arquivo_inseguro"


class ConversionError(PreloError):
    code = "falha_na_conversao"


class CancelledError(PreloError):
    code = "cancelado"
