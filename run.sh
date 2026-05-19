#!/usr/bin/env bash
# Executa o EpicPen diretamente do código-fonte (modo desenvolvimento).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Wayland nativo — sem sobrescrever QT_QPA_PLATFORM.
# Para forçar XWayland (depuração): QT_QPA_PLATFORM=xcb bash run.sh

export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_ACCESSIBILITY=0

exec python3 "$SCRIPT_DIR/src/main.py" "$@"
