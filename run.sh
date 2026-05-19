#!/usr/bin/env bash
# Executa o EpicPen diretamente do código-fonte (modo desenvolvimento).
# Usa XWayland (xcb) para máxima compatibilidade com overlay transparente
# e captura de tela — o backend Wayland nativo ainda não suporta grabWindow.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Força backend X11 via XWayland para overlay + screenshot funcionarem
export QT_QPA_PLATFORM=xcb

# Suprime avisos de acessibilidade não-críticos
export QT_ACCESSIBILITY=0

# Evita warnings de scaling em monitores HiDPI
export QT_AUTO_SCREEN_SCALE_FACTOR=1

exec python3 "$SCRIPT_DIR/src/main.py" "$@"
