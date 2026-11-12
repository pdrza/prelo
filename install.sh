#!/usr/bin/env bash
# Instala o Prelo para o usuário atual (sem sudo, salvo para pacotes do sistema).
#
#   ~/.local/share/prelo/venv       ambiente Python com o motor e a API
#   ~/.local/bin/prelo*             comandos (prelo, prelo-app, prelo-lote, ...)
#   ~/.local/share/applications     atalho no menu e "Abrir com Prelo"
#   ~/.local/share/prelo/uninstall.sh  desinstalador (a pasta de onde este script roda pode ser apagada)
set -euo pipefail

RAIZ="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=lib/comum.sh
source "$RAIZ/lib/comum.sh"
BIN_USUARIO="$HOME/.local/bin"
APLICATIVOS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONES="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
UTILITARIOS=(prelo-lote prelo-enviar prelo-vigiar)

ajuda() {
  cat <<'EOF'
Uso: ./install.sh [opções]

  --dev          instala em modo de desenvolvimento (o código do repositório é usado direto)
  --qt           janela com Qt WebEngine pelo pip, sem pacotes do sistema (~200 MB)
  --sem-janela   só linha de comando e API (a interface abre no navegador)
  -y, --sim      responde "sim" às perguntas (instala pacotes do sistema com sudo)
  -h, --ajuda    mostra esta ajuda
EOF
}

dev=0 janela=gtk sim=0
while (($#)); do
  case "$1" in
    --dev) dev=1 ;;
    --qt) janela=qt ;;
    --sem-janela) janela=nenhuma ;;
    -y | --sim) sim=1 ;;
    -h | --ajuda | --help) ajuda; exit 0 ;;
    *) morrer "opção desconhecida: $1" ;;
  esac
  shift
done

perguntar() {  # pergunta → 0 se "sim"
  ((sim)) && return 0
  [[ -t 0 ]] || return 1
  local r
  read -r -p "$1 [s/N] " r
  [[ "${r,,}" == s* ]]
}

# ---------------------------------------------------------------- Python
# O GTK do Python (python3-gi) é instalado para o Python do sistema. Um ambiente
# virtual ativo no terminal (o "(.venv)" que o VS Code ativa, por exemplo) não
# o enxerga; por isso o instalador usa o Python de verdade por trás dele.
titulo "Instalação do Prelo"
if [[ -n "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]]; then
  info "ambiente virtual ativo (${VIRTUAL_ENV:-$CONDA_PREFIX}): a instalação usa o Python do sistema"
fi
python_real() {  # interpretador → Python por trás de um venv (ou ele mesmo)
  "$1" -c 'import sys; print(getattr(sys, "_base_executable", "") or sys.executable)' 2> /dev/null
}
python_serve() {  # 3.11+ e com o módulo venv
  "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))' 2> /dev/null
}
tem_gtk() {
  "$1" -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1")' 2> /dev/null
}
escolher_python() {  # o primeiro Python que serve; com janela GTK, de preferência um que enxergue o gi
  local c real primeiro=""
  for c in ${PYTHON:+"$PYTHON"} /usr/bin/python3 "$(command -v python3 || true)" /usr/local/bin/python3; do
    [[ -n "$c" && -x "$c" ]] || continue
    real="$(python_real "$c")" || continue
    [[ -n "$real" && -x "$real" ]] || continue
    python_serve "$real" || continue
    [[ -z "$primeiro" ]] && primeiro="$real"
    if [[ "$janela" != gtk ]] || tem_gtk "$real"; then
      printf '%s\n' "$real"
      return 0
    fi
  done
  [[ -n "$primeiro" ]] && printf '%s\n' "$primeiro"
}
PYTHON="$(escolher_python || true)"
[[ -n "$PYTHON" ]] || morrer "preciso do Python 3.11 ou mais novo com o módulo venv (Ubuntu/Debian: sudo apt install python3 python3-venv)"
ok "$("$PYTHON" -V) em $PYTHON"

# ---------------------------------------------------------------- janela (GTK)
pacotes_gtk() {
  if command -v apt-get > /dev/null; then echo "apt-get install -y python3-gi gir1.2-webkit2-4.1"
  elif command -v dnf > /dev/null; then echo "dnf install -y python3-gobject webkit2gtk4.1"
  elif command -v pacman > /dev/null; then echo "pacman -S --needed python-gobject webkit2gtk-4.1"
  elif command -v zypper > /dev/null; then echo "zypper install -y python3-gobject typelib-1_0-WebKit2-4_1"
  fi
}
if [[ "$janela" == gtk ]] && ! tem_gtk "$PYTHON"; then
  cmd="$(pacotes_gtk || true)"
  aviso "faltam o GTK e o WebKitGTK para a janela nativa"
  if [[ -n "$cmd" ]] && perguntar "Instalar agora com: sudo $cmd ?"; then
    # shellcheck disable=SC2086  # comando montado acima, palavras separadas de propósito
    sudo $cmd
    PYTHON="$(escolher_python || true)"
  fi
  if ! tem_gtk "$PYTHON"; then
    aviso "nenhum Python 3.11+ do sistema enxerga o GTK: seguindo sem janela nativa (o Prelo abre no navegador)"
    info "alternativa com janela, sem pacotes do sistema: ./install.sh --qt"
    janela=nenhuma
  fi
fi
[[ "$janela" == gtk ]] && ok "GTK 3 + WebKitGTK 4.1"

# ---------------------------------------------------------------- ambiente
VENV="$PRELO_DADOS/venv"
mkdir -p "$PRELO_DADOS"
if [[ -x "$VENV/bin/python" ]] && ! "$VENV/bin/python" -c 'import sys' 2> /dev/null; then
  rm -rf "$VENV"  # ambiente quebrado (Python do sistema atualizado)
fi
if [[ "$janela" == gtk && -x "$VENV/bin/python" ]] && ! tem_gtk "$VENV/bin/python"; then
  info "recriando o ambiente para enxergar o GTK do sistema"
  rm -rf "$VENV"  # instalação anterior sem janela (ou feita com outro Python)
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  info "criando o ambiente Python em $VENV"
  opcoes_venv=()
  [[ "$janela" == gtk ]] && opcoes_venv=(--system-site-packages)  # enxerga o "gi" do sistema
  "$PYTHON" -m venv "${opcoes_venv[@]}" "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip

extra=""
case "$janela" in
  gtk) extra="[desktop]" ;;
  qt) extra="[desktop-qt]" ;;
esac
info "instalando o Prelo e as bibliotecas (pode levar alguns minutos)"
if ((dev)); then
  "$VENV/bin/python" -m pip install --quiet -e "$RAIZ$extra"
else
  "$VENV/bin/python" -m pip install --quiet --upgrade "$RAIZ$extra"
fi
"$VENV/bin/prelo" profiles > /dev/null || morrer "o Prelo foi instalado mas não roda; veja as mensagens acima"
ok "Prelo $("$VENV/bin/python" -c 'import prelo; print(prelo.__version__)')"

# ---------------------------------------------------------------- comandos
mkdir -p "$BIN_USUARIO" "$PRELO_DADOS/bin" "$PRELO_DADOS/lib"
if ((dev)); then
  for u in "${UTILITARIOS[@]}"; do ln -sfn "$RAIZ/bin/$u" "$PRELO_DADOS/bin/$u"; done
  ln -sfn "$RAIZ/lib/comum.sh" "$PRELO_DADOS/lib/comum.sh"
  ln -sfn "$RAIZ/uninstall.sh" "$PRELO_DADOS/uninstall.sh"
else
  install -m 755 "${UTILITARIOS[@]/#/$RAIZ/bin/}" "$PRELO_DADOS/bin/"
  install -m 644 "$RAIZ/lib/comum.sh" "$PRELO_DADOS/lib/"
  install -m 755 "$RAIZ/uninstall.sh" "$PRELO_DADOS/"  # a pasta do pacote pode ser apagada depois
fi
ln -sfn "$VENV/bin/prelo" "$BIN_USUARIO/prelo"
ln -sfn "$VENV/bin/prelo-app" "$BIN_USUARIO/prelo-app"
for u in "${UTILITARIOS[@]}"; do ln -sfn "$PRELO_DADOS/bin/$u" "$BIN_USUARIO/$u"; done
ok "comandos em $BIN_USUARIO: prelo, prelo-app, ${UTILITARIOS[*]}"

# ---------------------------------------------------------------- atalho
mkdir -p "$APLICATIVOS" "$ICONES/scalable/apps" "$ICONES/256x256/apps"
install -m 644 "$RAIZ/src/prelo/ui/img/icon.svg" "$ICONES/scalable/apps/prelo.svg"
install -m 644 "$RAIZ/src/prelo/ui/img/icon-256.png" "$ICONES/256x256/apps/prelo.png"
cat > "$APLICATIVOS/prelo.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Prelo
GenericName=Conversor de livros
Comment=Converte PDF, EPUB e quadrinhos para Kindle, Kobo e outros, mantendo o layout
Exec=$BIN_USUARIO/prelo-app %F
Icon=prelo
Terminal=false
Categories=Office;Viewer;Utility;
Keywords=ebook;livro;kindle;kobo;pdf;epub;azw3;mobi;conversor;
MimeType=application/pdf;application/epub+zip;application/x-mobipocket-ebook;application/vnd.amazon.mobi8-ebook;application/vnd.amazon.ebook;application/vnd.comicbook+zip;application/x-cbz;
StartupNotify=true
StartupWMClass=prelo
EOF
command -v update-desktop-database > /dev/null && update-desktop-database -q "$APLICATIVOS" || true
command -v gtk-update-icon-cache > /dev/null && gtk-update-icon-cache -q -t "$ICONES" 2> /dev/null || true
ok "atalho no menu de aplicativos e em \"Abrir com\" para PDF, EPUB, AZW3, MOBI e CBZ"

# ---------------------------------------------------------------- final
echo >&2
case ":$PATH:" in
  *":$BIN_USUARIO:"*) ;;
  *) aviso "$BIN_USUARIO não está no PATH; adicione ao ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
for c in prelo prelo-app; do  # um venv ativo no terminal pode esconder os comandos instalados
  achado="$(command -v "$c" || true)"
  if [[ -n "$achado" && "$achado" != "$BIN_USUARIO/$c" ]]; then
    dica="abra o Prelo pelo menu ou use $BIN_USUARIO/$c"
    [[ -n "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]] && dica="rode 'deactivate' ou abra o Prelo pelo menu"
    aviso "neste terminal, '$c' é $achado, não o recém-instalado: $dica"
  fi
done
command -v inotifywait > /dev/null || info "opcional: inotify-tools deixa o prelo-vigiar instantâneo (sem ele, verifica a cada 5 s)"
command -v ebook-convert > /dev/null || info "opcional: o Calibre pode ser usado como motor alternativo de AZW3/MOBI"
info "para desinstalar: $PRELO_DADOS/uninstall.sh"
titulo "Pronto. Abra o Prelo pelo menu ou rode: prelo-app"
