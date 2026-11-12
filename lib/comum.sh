#!/usr/bin/env bash
# Funções comuns aos utilitários do Prelo (prelo-lote, prelo-enviar, prelo-vigiar).
# Uso: source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/comum.sh"
# shellcheck disable=SC2034  # variáveis usadas pelos scripts que carregam este arquivo

PRELO_DADOS="${XDG_DATA_HOME:-$HOME/.local/share}/prelo"
PRELO_ESTADO="${XDG_STATE_HOME:-$HOME/.local/state}/prelo"
PRELO_EXTENSOES="azw azw3 bmp cbz epub gif jpeg jpg kepub mobi pdb pdf png prc tif tiff webp zip"  # = formats.BOOK_EXTENSIONS (conferido nos testes)
PRELO_FORMATOS="epub azw3 mobi cbz pdf txt"

if [[ -t 2 ]]; then
  _negrito=$'\e[1m' _apagado=$'\e[2m' _vermelho=$'\e[31m' _verde=$'\e[32m' _amarelo=$'\e[33m' _fim=$'\e[0m'
else
  _negrito='' _apagado='' _vermelho='' _verde='' _amarelo='' _fim=''
fi

info()   { printf '%s›%s %s\n' "$_apagado" "$_fim" "$*" >&2; }
ok()     { printf '%s✓%s %s\n' "$_verde" "$_fim" "$*" >&2; }
aviso()  { printf '%s⚠%s %s\n' "$_amarelo" "$_fim" "$*" >&2; }
erro()   { printf '%s✗%s %s\n' "$_vermelho" "$_fim" "$*" >&2; }
titulo() { printf '%s%s%s\n' "$_negrito" "$*" "$_fim" >&2; }
morrer() { erro "$*"; exit 1; }

# Caminho do comando "prelo" (motor em Python): variável PRELO_BIN, ambiente do
# repositório (desenvolvimento), ambiente instalado pelo install.sh ou PATH.
prelo_cmd() {
  if [[ -n "${PRELO_BIN:-}" ]]; then
    printf '%s\n' "$PRELO_BIN"
    return
  fi
  local aqui candidato
  aqui="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
  for candidato in "$aqui/../.venv/bin/prelo" "$PRELO_DADOS/venv/bin/prelo" "$(command -v prelo || true)"; do
    if [[ -n "$candidato" && -x "$candidato" ]]; then
      printf '%s\n' "$candidato"
      return
    fi
  done
  morrer "comando 'prelo' não encontrado. Rode ./install.sh"
}

minusculas() { printf '%s' "${1,,}"; }

extensao() {
  local nome="${1##*/}"
  [[ "$nome" == *.* ]] || return 1
  minusculas "${nome##*.}"
}

# É um arquivo que o Prelo aceita como entrada?
e_livro() {
  local ext nome="${1##*/}"
  [[ -f "$1" && "$nome" != .* ]] || return 1
  ext="$(extensao "$1")" || return 1
  [[ " $PRELO_EXTENSOES " == *" $ext "* ]]
}

formato_valido() { [[ " $PRELO_FORMATOS " == *" $1 "* ]]; }

hash_arquivo() { sha256sum -- "$1" | cut -d' ' -f1; }

# Manifesto: uma linha por conversão feita (hash do original, formato, perfil,
# arquivo gerado). Reconhece o mesmo livro mesmo renomeado ou movido.
manifesto_tem() {  # manifesto hash formato perfil
  [[ -f "$1" ]] && grep -q -- "^$2"$'\t'"$3"$'\t'"$4"$'\t' "$1"
}

manifesto_anota() {  # manifesto hash formato perfil saida
  mkdir -p "$(dirname "$1")"
  (
    flock 9
    printf '%s\t%s\t%s\t%s\t%s\n' "$2" "$3" "$4" "$5" "$(date -Iseconds)" >> "$1"
  ) 9> "$1.lock"
}

# Converte um arquivo; imprime o caminho gerado. O log da conversão vai para $4.
# converter ORIGEM FORMATO PASTA_SAIDA LOG [opções extras do prelo convert...]
converter() {
  local origem="$1" formato="$2" pasta="$3" log="$4"
  shift 4
  mkdir -p "$pasta"
  "$(prelo_cmd)" convert "$origem" -f "$formato" -o "$pasta" --print-path "$@" 2>> "$log"
}

notificar() {  # titulo texto
  if command -v notify-send > /dev/null && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    notify-send -a Prelo -i prelo "$1" "$2" 2> /dev/null || true
  fi
}

arquivo_de_log() {  # prefixo
  mkdir -p "$PRELO_ESTADO"
  printf '%s/%s-%s.log\n' "$PRELO_ESTADO" "$1" "$(date +%Y%m%d-%H%M%S)"
}
