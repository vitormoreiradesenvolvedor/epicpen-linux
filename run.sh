#!/usr/bin/env bash
# Executa o EpicPen diretamente do código-fonte (modo desenvolvimento).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Wayland nativo — sem sobrescrever QT_QPA_PLATFORM.
# Para forçar XWayland (depuração): QT_QPA_PLATFORM=xcb bash run.sh

export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_ACCESSIBILITY=0

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "[run.sh] venv não encontrado em .venv/ — rode: python3 -m venv --copies .venv --system-site-packages && .venv/bin/pip install 'PyQt6==6.10.1'"
    echo "         (--copies: intérprete próprio → captura silenciosa via KWin autorizável)"
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/src/main.py" "$@"
