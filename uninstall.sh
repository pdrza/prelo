#!/usr/bin/env bash
# Remove o Prelo do usuário atual. A estante e as preferências ficam, salvo --tudo.
set -euo pipefail

RAIZ="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=lib/comum.sh
source "$RAIZ/lib/comum.sh"
BIN_USUARIO="$HOME/.local/bin"
DADOS_XDG="${XDG_DATA_HOME:-$HOME/.local/share}"

tudo=0
case "${1:-}" in
  --tudo) tudo=1 ;;
  -h | --ajuda | --help) echo "Uso: ./uninstall.sh [--tudo]   (--tudo apaga também a estante e as preferências)"; exit 0 ;;
  "") ;;
  *) morrer "opção desconhecida: $1" ;;
esac

if systemctl --user list-unit-files prelo-vigiar.service > /dev/null 2>&1; then
  systemctl --user disable --now prelo-vigiar.service 2> /dev/null || true
fi
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/prelo-vigiar.service"
systemctl --user daemon-reload 2> /dev/null || true

for c in prelo prelo-app prelo-lote prelo-enviar prelo-vigiar; do
  [[ -L "$BIN_USUARIO/$c" ]] && rm -f "$BIN_USUARIO/$c"
done
rm -f "$DADOS_XDG/applications/prelo.desktop" \
  "$DADOS_XDG/icons/hicolor/scalable/apps/prelo.svg" \
  "$DADOS_XDG/icons/hicolor/256x256/apps/prelo.png"
command -v update-desktop-database > /dev/null && update-desktop-database -q "$DADOS_XDG/applications" || true
rm -rf "${PRELO_DADOS:?}/venv" "${PRELO_DADOS:?}/bin" "${PRELO_DADOS:?}/lib" "${PRELO_DADOS:?}/uninstall.sh"

if ((tudo)); then
  rm -rf "${PRELO_DADOS:?}" "${PRELO_ESTADO:?}"
  ok "Prelo removido, com a estante e as preferências"
else
  ok "Prelo removido. A estante e as preferências continuam em $PRELO_DADOS (apague com --tudo)"
fi
