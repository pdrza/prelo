"""Limpeza e estrutura de texto (usado no modo texto fluido).

Do pré-processamento do Calibre:

* ligaduras viram letras (ﬁ → fi);
* acentos soltos, comuns em PDFs, são recompostos (´e → é);
* palavras hifenizadas no fim da linha são juntadas consultando o próprio livro.

Melhorias:

* hifenização com regras do português: hífen repetido no início da linha
  seguinte indica palavra composta (guarda-/-chuva), e pronomes oblíquos mantêm
  o hífen (disse-/lhe → disse-lhe);
* detecção de capítulos em português, inglês e espanhol (o padrão do Calibre
  só reconhece inglês: *chapter, book, part...*).
"""

from __future__ import annotations

import re
import unicodedata

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
_LIG_RE = re.compile("|".join(LIGATURES))

# acento "de espaçamento" → acento combinante
SPACING_ACCENTS = {
    "´": "́", "ˊ": "́", "ˋ": "̀", "ˆ": "̂",
    "˜": "̃", "¨": "̈", "¸": "̧", "ˇ": "̌",
}
_ACC = "".join(SPACING_ACCENTS)
# acento antes da letra (como o pdftohtml produz): só vogais, ç e ñ, para "a´gua" não virar "aǵua"
_ACCENT_BEFORE = re.compile(rf"([{_ACC}])\s?([aeiouyAEIOUYcCnN])")
_ACCENT_AFTER = re.compile(rf"([A-Za-z])([{_ACC}])")
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_PUA = re.compile("[-�]")


def _compose(base: str, accent: str) -> str | None:
    c = unicodedata.normalize("NFC", base + SPACING_ACCENTS[accent])
    return c if len(c) == 1 else None


def fix_spacing_accents(s: str) -> str:
    if not any(a in s for a in SPACING_ACCENTS):
        return s
    s = _ACCENT_BEFORE.sub(lambda m: _compose(m.group(2), m.group(1)) or m.group(0), s)
    return _ACCENT_AFTER.sub(lambda m: _compose(m.group(1), m.group(2)) or m.group(0), s)


def clean_text(s: str) -> str:
    if not s:
        return s
    s = _LIG_RE.sub(lambda m: LIGATURES[m.group(0)], s)
    s = fix_spacing_accents(s)
    s = _ZERO_WIDTH.sub("", s)
    return unicodedata.normalize("NFC", s)


def garbled_ratio(s: str) -> float:
    """Parcela de caracteres ilegíveis (área de uso privado / substituição):
    sinal de fonte sem mapeamento Unicode, em que extrair texto dá lixo."""
    s = "".join(s.split())
    if not s:
        return 0.0
    return len(_PUA.findall(s)) / len(s)


# ------------------------------------------------------------ hifenização
CLITICS = {
    "me", "te", "se", "lhe", "lhes", "o", "a", "os", "as", "lo", "la", "los", "las",
    "no", "na", "nos", "nas", "vos", "mo", "ma", "mos", "mas", "to", "ta", "tos", "tas",
    "lho", "lha", "lhos", "lhas",
}
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")


def vocabulary(text: str) -> tuple[set[str], set[str]]:
    """Palavras do próprio livro: (simples, com hífen). Base da decisão de
    juntar ou não uma palavra quebrada no fim da linha."""
    plain: set[str] = set()
    hyphenated: set[str] = set()
    for w in _WORD_RE.findall(text):
        lw = w.lower()
        (hyphenated if "-" in lw else plain).add(lw)
    return plain, hyphenated


def join_hyphenated(left: str, right: str, plain: set[str], hyphenated: set[str]) -> tuple[str, str]:
    """Junta ``left`` (termina em hífen) e ``right`` (começo da próxima linha).

    Devolve ``(texto_juntado, separador_usado)``.
    """
    stem = left[:-1]
    m_left = re.search(r"([^\W\d_]+)$", stem)
    m_right = re.match(r"(-?)([^\W\d_]+)", right)
    if not m_left or not m_right:
        return left + right, ""
    lw, rw = m_left.group(1), m_right.group(2)
    if m_right.group(1) == "-":  # guarda-/-chuva: convenção de repetir o hífen
        return stem + "-" + right[1:], "-"
    if not rw[0].islower():  # pós-/Graduação, 1990-/2000
        return stem + "-" + right, "-"
    compound = f"{lw}-{rw}".lower()
    joined = (lw + rw).lower()
    if joined in plain:  # a palavra aparece inteira em outro lugar do livro
        return stem + right, ""
    if compound in hyphenated or rw.lower() in CLITICS:  # guarda-chuva, disse-lhe
        return stem + "-" + right, "-"
    return stem + right, ""  # quebra de sílaba comum


# -------------------------------------------------------------- capítulos
_NUM = (r"(?:\d{1,4}|[ivxlcdm]{1,8}|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|"
        r"onze|doze|treze|qu?atorze|catorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|"
        r"primeir[oa]|segund[oa]|terceir[oa]|quart[oa]|quint[oa]|sext[oa]|s[eé]tim[oa]|oitav[oa]|"
        r"non[oa]|d[eé]cim[oa]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"uno|dos|cuatro|siete|ocho|nueve|diez|[a-z])")
_CHAPTER_WORDS = (r"cap[íi]tulo|cap\.|chapter|chap\.|parte|part|livro|book|libro|"
                  r"se[çc][ãa]o|section|secci[óo]n|ato|act|acto")
_STANDALONE = (r"pr[óo]logo|prologue|ep[íi]logo|epilogue|introdu[çc][ãa]o|introduction|introducci[óo]n|"
               r"pref[áa]cio|preface|prefacio|apresenta[çc][ãa]o|posf[áa]cio|afterword|ap[êe]ndice|"
               r"appendix|ap[ée]ndice|conclus[ãa]o|conclusion|conclusi[óo]n|agradecimentos|"
               r"acknowledge?ments|agradecimientos|interl[úu]dio|interlude|nota do autor|notas|"
               r"bibliografia|bibliography|gloss[áa]rio|glossary|dedicat[óo]ria")
CHAPTER_RE = re.compile(rf"^\s*(?:(?:{_CHAPTER_WORDS})\s+{_NUM}\b|(?:{_STANDALONE})\b)", re.I)
NUMERAL_ONLY_RE = re.compile(r"^\s*(?:\d{1,3}|[IVXLCDM]{1,7})\s*\.?\s*$")


def is_chapter_title(text: str, extra: re.Pattern | None = None) -> bool:
    text = text.strip()
    if not text or len(text) > 120:
        return False
    if CHAPTER_RE.match(text):
        return True
    return bool(extra and extra.search(text))


# ------------------------------------------------- cabeçalhos e rodapés
_PAGE_NUM_RE = re.compile(
    r"^\s*[-–—]?\s*(?:p[áa]g(?:ina)?\.?\s*|page\s*)?(\d{1,4}|[ivxlcdm]{1,7})"
    r"\s*(?:(?:/|de|of)\s*\d{1,4})?\s*[-–—]?\s*$", re.I)


def is_page_number(text: str) -> bool:
    return bool(_PAGE_NUM_RE.match(text))


def repeat_key(text: str) -> str:
    """Chave para achar linhas repetidas entre páginas: números viram '#'."""
    t = text.lower().strip()
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"\b[ivxlcdm]{1,7}\b", "#", t)
    return re.sub(r"\s+", " ", t)


_TERMINAL = ".!?…:;\"”»)'’"


def ends_sentence(text: str) -> bool:
    t = text.rstrip()
    return bool(t) and t[-1] in _TERMINAL


def starts_lowercase(text: str) -> bool:
    t = text.lstrip(" \t\"“«'‘(—–-")
    return bool(t) and t[0].islower()
