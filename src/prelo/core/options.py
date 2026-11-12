"""Opções de conversão com níveis de recomendação.

Regras herdadas do Calibre (``plumber.py``):

* Cada opção tem um valor e um nível: LOW, MED ou HIGH.
* As camadas entram nesta ordem e só substituem a anterior com nível igual ou
  maior: padrão do código (LOW) → recomendações dos plugins e do dispositivo
  (MED/HIGH) → preferências salvas (LOW) → configuração salva do livro (HIGH)
  → escolha feita na hora, pela interface, CLI ou API (HIGH).
* Recomendação HIGH de plugin **trava** a opção: nada a substitui (ex.: saída
  CBZ sempre usa layout fixo).

O que mudamos em relação ao Calibre:

* Nome de opção desconhecido é **erro**, com sugestão do nome mais parecido.
  No Calibre, recomendações com nome errado (``page_breaks_brefore``,
  ``dont_split_on_pagebreaks``) são ignoradas em silêncio e nunca têm efeito.
* Toda opção recusada ou travada fica registrada em ``OptionSet.notes`` para a
  interface explicar ao usuário por que a escolha dele não valeu.
* Opções perigosas (``dangerous``) são recusadas quando chegam pela rede, como
  o servidor de conteúdo do Calibre faz com ``debug_pipeline`` e afins.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable, Mapping, NamedTuple

from .errors import InvalidOptionError, UnknownOptionError
from .profiles import PROFILES


class Level(IntEnum):
    LOW = 1
    MED = 2
    HIGH = 3


class Recommendation(NamedTuple):
    name: str
    value: Any
    level: Level


@dataclass(frozen=True)
class OptionSpec:
    name: str
    kind: str  # bool | int | float | choice | str
    default: Any
    group: str
    label: str
    help: str
    choices: tuple[tuple[Any, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    dangerous: bool = False
    secret: bool = False

    @property
    def cli_flag(self) -> str:
        return "--" + self.name.replace("_", "-")

    def coerce(self, value: Any) -> Any:
        """Converte e valida um valor vindo da interface, CLI ou API."""
        try:
            if self.kind == "bool":
                if isinstance(value, str):
                    low = value.strip().lower()
                    if low in ("1", "true", "sim", "yes", "on"):
                        return True
                    if low in ("0", "false", "nao", "não", "no", "off", ""):
                        return False
                    raise ValueError(value)
                return bool(value)
            if self.kind == "int":
                result: Any = int(float(value))
            elif self.kind == "float":
                result = float(value)
            elif self.kind == "choice":
                allowed = [c[0] for c in self.choices]
                if value not in allowed:
                    # aceita "3" para choices inteiros
                    for c in allowed:
                        if str(c) == str(value):
                            return c
                    raise ValueError(value)
                return value
            else:
                return "" if value is None else str(value)
        except (TypeError, ValueError):
            raise InvalidOptionError(
                f"Valor inválido para '{self.label}': {value!r}",
                hint=self._describe_allowed(),
            ) from None
        if self.minimum is not None and result < self.minimum:
            raise InvalidOptionError(f"'{self.label}' deve ser no mínimo {self.minimum}.")
        if self.maximum is not None and result > self.maximum:
            raise InvalidOptionError(f"'{self.label}' deve ser no máximo {self.maximum}.")
        return result

    def _describe_allowed(self) -> str:
        if self.kind == "choice":
            return "Valores aceitos: " + ", ".join(str(c[0]) for c in self.choices)
        if self.minimum is not None and self.maximum is not None:
            return f"Use um número entre {self.minimum} e {self.maximum}."
        return ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "default": self.default,
            "group": self.group, "label": self.label, "help": self.help,
            "choices": [{"value": v, "label": l} for v, l in self.choices],
            "minimum": self.minimum, "maximum": self.maximum,
            "dangerous": self.dangerous,
        }


OPTIONS: dict[str, OptionSpec] = {}


def _opt(name: str, kind: str, default: Any, group: str, label: str, help: str, **kw: Any) -> None:
    if name in OPTIONS:  # pragma: no cover - proteção contra duplicidade
        raise RuntimeError(f"opção duplicada: {name}")
    OPTIONS[name] = OptionSpec(name, kind, default, group, label, help, **kw)


# ---------------------------------------------------------------- Conversão
_opt("mode", "choice", "auto", "Conversão", "Modo de conversão",
     "Automático analisa o arquivo e escolhe. Layout fiel mantém cada página idêntica ao "
     "original. Texto fluido extrai o texto para ele se adaptar à tela do leitor.",
     choices=(("auto", "Automático"), ("fixed", "Layout fiel"), ("reflow", "Texto fluido")))
_opt("profile", "choice", "auto", "Conversão", "Dispositivo",
     "Tela de destino. Define a resolução das páginas e se o leitor é colorido ou e-ink.",
     choices=(("auto", "Automático (pelo formato)"),) + tuple((p.id, p.name) for p in PROFILES))
_opt("quality", "choice", "alta", "Conversão", "Qualidade das páginas",
     "Compressão das imagens de página no layout fiel.",
     choices=(("maxima", "Máxima (sem perdas)"), ("alta", "Alta"),
              ("equilibrada", "Equilibrada"), ("compacta", "Compacta")))
_opt("color_mode", "choice", "auto", "Conversão", "Cores",
     "Automático usa tons de cinza em telas e-ink monocromáticas e cor nas demais.",
     choices=(("auto", "Automático"), ("color", "Colorido"), ("grayscale", "Tons de cinza")))
_opt("dpi", "int", 0, "Conversão", "Resolução (DPI)",
     "Usado quando o dispositivo é 'Original'. 0 escolhe automaticamente (200 DPI).",
     minimum=0, maximum=600)
_opt("page_range", "str", "", "Conversão", "Páginas",
     "Intervalo de páginas a converter, ex.: 1-20,25. Vazio converte tudo.")
_opt("password", "str", "", "Conversão", "Senha do PDF",
     "Senha de abertura, se o PDF pedir uma.", secret=True)

# ------------------------------------------------------------------- Página
_opt("temperature", "int", 6500, "Página", "Temperatura (K)",
     "Cor do papel. 6500 K é neutro; valores menores deixam a página mais quente "
     "(amarelada), maiores deixam mais fria (azulada).", minimum=1900, maximum=10000)
_opt("brightness", "int", 0, "Página", "Brilho", "De -100 a 100.", minimum=-100, maximum=100)
_opt("contrast", "int", 0, "Página", "Contraste", "De -100 a 100.", minimum=-100, maximum=100)
_opt("gamma", "float", 1.0, "Página", "Gama",
     "Acima de 1 escurece os meios-tons (texto fica mais encorpado em e-ink).",
     minimum=0.3, maximum=3.0)
_opt("saturation", "int", 0, "Página", "Saturação", "De -100 a 100.", minimum=-100, maximum=100)
_opt("invert", "bool", False, "Página", "Modo noturno",
     "Inverte claro e escuro mantendo as cores (fundo escuro, texto claro).")
_opt("sharpen", "bool", False, "Página", "Nitidez", "Realça bordas. Útil em PDFs digitalizados.")
_opt("normalize", "bool", False, "Página", "Normalizar contraste",
     "Estica os tons para usar do preto ao branco. Útil em digitalizações apagadas.")
_opt("trim_margins", "choice", "none", "Página", "Margens",
     "Cortar margens deixa o texto maior na tela. O corte uniforme usa a mesma área em "
     "todas as páginas (separando pares e ímpares) para o tamanho da letra não variar.",
     choices=(("none", "Não cortar"), ("uniform", "Corte uniforme"), ("page", "Corte por página")))
_opt("split_spreads", "bool", False, "Página", "Dividir páginas duplas",
     "Páginas mais largas que altas viram duas, na ordem de leitura.")
_opt("fill_screen", "bool", True, "Página", "Preencher a tela",
     "Completa a página até o tamanho exato da tela com a cor do papel, sem faixas.")
_opt("apply_to_images", "bool", False, "Página", "Ajustar imagens do texto fluido",
     "No texto fluido, aplica temperatura e demais ajustes também às imagens.")

# -------------------------------------------------------------------- Texto
_opt("remove_headers_footers", "bool", True, "Texto", "Remover cabeçalhos e rodapés",
     "Detecta linhas repetidas no topo e no pé das páginas (títulos correntes, números).")
_opt("dehyphenate", "bool", True, "Texto", "Juntar palavras hifenizadas",
     "Junta palavras quebradas no fim da linha, mantendo o hífen de palavras compostas "
     "e de pronomes (ex.: disse-lhe).")
_opt("detect_chapters", "bool", True, "Texto", "Detectar capítulos",
     "Reconhece Capítulo, Parte, Prólogo, Epílogo etc. em português, inglês e espanhol.")
_opt("chapter_pattern", "str", "", "Texto", "Padrão extra de capítulo",
     "Expressão regular adicional para reconhecer títulos de capítulo.")
_opt("keep_page_numbers", "bool", True, "Texto", "Manter números de página",
     "Guarda a paginação do original (lista de páginas do EPUB 3).")
_opt("tables", "choice", "html", "Texto", "Tabelas",
     "Como levar tabelas do PDF para o texto fluido.",
     choices=(("html", "Tabela de verdade"), ("image", "Imagem"), ("text", "Texto corrido")))
_opt("vector_graphics", "bool", True, "Texto", "Gráficos vetoriais como imagem",
     "Gráficos desenhados no PDF (não são imagens) viram figuras em vez de sumirem.")
_opt("text_align", "choice", "justify", "Texto", "Alinhamento",
     "Alinhamento sugerido ao leitor.",
     choices=(("justify", "Justificado"), ("left", "À esquerda")))
_opt("paragraph_style", "choice", "indent", "Texto", "Parágrafos",
     "Recuo na primeira linha (estilo de livro) ou espaço entre parágrafos.",
     choices=(("indent", "Recuo"), ("spaced", "Espaçado")))
_opt("cover_page", "int", 1, "Texto", "Página da capa",
     "Página do PDF usada como capa.", minimum=1, maximum=100000)

# ------------------------------------------------------------------- Kindle
_opt("kindle_doc_type", "choice", "EBOK", "Kindle", "Aparecer no Kindle como",
     "Livro aparece em Livros; Documento aparece em Documentos.",
     choices=(("EBOK", "Livro"), ("PDOC", "Documento pessoal")))
_opt("kindle_engine", "choice", "native", "Kindle", "Motor AZW3/MOBI",
     "Nativo é o gerador do Prelo. Calibre usa o ebook-convert instalado.",
     choices=(("native", "Nativo"), ("calibre", "Calibre (se instalado)")))
_opt("inline_toc", "choice", "auto", "Kindle", "Sumário no fim do livro",
     "Automático adiciona no MOBI (que não tem menu de capítulos) e omite no AZW3.",
     choices=(("auto", "Automático"), ("yes", "Sim"), ("no", "Não")))

# ---------------------------------------------------------------------- PDF
_opt("pdf_text_layer", "bool", True, "PDF", "Manter texto pesquisável",
     "Na saída PDF, mantém o texto original por baixo das páginas ajustadas.")

# ---------------------------------------------------------------- Avançado
_opt("debug_pipeline", "str", "", "Avançado", "Pasta de depuração",
     "Salva o livro em cada etapa (entrada, transformações, saída) nesta pasta.",
     dangerous=True)
_opt("workers", "int", 0, "Avançado", "Processos de renderização",
     "0 usa o número de núcleos disponíveis.", minimum=0, maximum=32)


@dataclass
class OptionValue:
    value: Any
    level: Level
    source: str
    locked: bool = False


@dataclass
class OptionSet:
    """Valores efetivos de uma conversão, com origem e nível de cada um."""

    values: dict[str, OptionValue] = field(default_factory=dict)
    notes: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.values:
            self.values = {n: OptionValue(s.default, Level.LOW, "padrão") for n, s in OPTIONS.items()}

    def __getattr__(self, name: str) -> Any:  # opts.temperature
        values = self.__dict__.get("values")
        if values is not None and name in values:
            return values[name].value
        raise AttributeError(name)

    def get(self, name: str) -> Any:
        return self.values[name].value

    def source(self, name: str) -> str:
        return self.values[name].source

    def apply(self, values: Mapping[str, Any] | None, level: Level, source: str, *,
              lock: bool = False, from_network: bool = False) -> None:
        for name, raw in (values or {}).items():
            spec = OPTIONS.get(name)
            if spec is None:
                raise unknown_option(name)
            if from_network and spec.dangerous:
                self.notes.append({"option": name, "kind": "bloqueada",
                                   "message": f"'{spec.label}' não pode ser definida pela rede."})
                continue
            value = spec.coerce(raw)
            current = self.values[name]
            if current.locked:
                if value != current.value:
                    self.notes.append({
                        "option": name, "kind": "travada",
                        "message": f"'{spec.label}' ficou em {_fmt(spec, current.value)}: "
                                   f"exigência de {current.source}.",
                    })
                continue
            if level >= current.level:
                self.values[name] = OptionValue(value, level, source, locked=lock and level == Level.HIGH)
            elif value != current.value:
                self.notes.append({
                    "option": name, "kind": "ignorada",
                    "message": f"'{spec.label}' de {source} não substituiu a recomendação de "
                               f"{current.source}.",
                })

    def recommend(self, recs: Iterable[Recommendation], source: str) -> None:
        """Recomendações de plugins/dispositivos. HIGH trava a opção."""
        for rec in recs:
            if rec.name not in OPTIONS:
                raise unknown_option(rec.name)
            current = self.values[rec.name]
            if current.locked:
                continue
            if rec.level >= current.level:
                value = OPTIONS[rec.name].coerce(rec.value)
                self.values[rec.name] = OptionValue(value, rec.level, source, locked=rec.level == Level.HIGH)

    def as_dict(self) -> dict[str, Any]:
        return {n: v.value for n, v in self.values.items()}

    def report(self) -> list[dict]:
        """Opções que diferem do padrão, com a origem (para o log e a interface)."""
        out = []
        for name, v in self.values.items():
            spec = OPTIONS[name]
            if v.value == spec.default and v.source == "padrão":
                continue
            out.append({"option": name, "label": spec.label,
                        "value": "•••" if spec.secret and v.value else v.value,
                        "level": v.level.name, "source": v.source, "locked": v.locked})
        return out


def unknown_option(name: str) -> UnknownOptionError:
    close = difflib.get_close_matches(name, OPTIONS.keys(), n=1)
    hint = f"Você quis dizer '{close[0]}'?" if close else "Veja a lista em GET /api/options."
    return UnknownOptionError(f"Opção desconhecida: '{name}'.", hint=hint)


def _fmt(spec: OptionSpec, value: Any) -> str:
    for v, label in spec.choices:
        if v == value:
            return label
    if isinstance(value, bool):
        return "sim" if value else "não"
    return str(value)


def validate_recommendations(recs: Iterable[Recommendation]) -> None:
    """Falha cedo se um plugin recomendar uma opção que não existe."""
    for rec in recs:
        if rec.name not in OPTIONS:
            raise unknown_option(rec.name)
        OPTIONS[rec.name].coerce(rec.value)
