#!/usr/bin/env bash
# Build script para gerar o AppImage do EpicPen Linux.
# Uso: bash scripts/build_appimage.sh
# Requer: python3; appimagetool no PATH ou em ./tools/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
APPDIR="$ROOT/AppDir"
VERSION=$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null || echo "0.1.0-dev")
ARCH=$(uname -m)
OUTPUT="$ROOT/EpicPen-${VERSION}-${ARCH}.AppImage"

echo "══════════════════════════════════════════"
echo "  EpicPen Linux — Build AppImage v${VERSION}"
echo "══════════════════════════════════════════"

# ── 1. Gera ícone PNG ──────────────────────────────────────────────────
echo "→ Gerando ícone..."
python3 "$SCRIPT_DIR/generate_icon.py"

# ── 2. Prepara AppDir ─────────────────────────────────────────────────
echo "→ Preparando AppDir..."
rm -rf "$APPDIR/usr/bin" "$APPDIR/usr/lib/epicpen-venv"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/lib" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# ── 3. Instala dependências Python em venv isolado ─────────────────────
# PyQt6==6.10.1 → Qt 6.10.0 bundled: mesma série minor da lib sistema (6.10.x)
# garantindo compatibilidade ABI com libLayerShellQtInterface compilada para Qt 6.10.
echo "→ Instalando dependências no venv (PyQt6==6.10.1)..."
VENV="$APPDIR/usr/lib/epicpen-venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet "PyQt6==6.10.1"

# Descobre caminho dos plugins do PyQt6 bundled
PYQT6_PLUGINS=$("$VENV/bin/python" -c \
  "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins'))")
PYQT6_LIBS=$("$VENV/bin/python" -c \
  "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'lib'))")
echo "  PyQt6 Qt version: $("$VENV/bin/python" -c 'from PyQt6.QtCore import QT_VERSION_STR; print(QT_VERSION_STR)')"

# ── 4. Copia fontes e recursos ────────────────────────────────────────
echo "→ Copiando fontes e recursos..."
cp -r "$ROOT/src/"* "$APPDIR/usr/bin/"
# tray.py e icons.py usam Path(__file__).parent.parent / "resources"
# → no AppImage isso resolve para usr/resources/
cp -r "$ROOT/resources" "$APPDIR/usr/resources"

# ── 5. Copia libs nativas do layer-shell ──────────────────────────────
echo "→ Copiando libs layer-shell..."

# libLayerShellQtInterface: carregada por ctypes em layershell.py.
# A path local (usr/lib/) tem prioridade sobre _LIB_SYSTEM em layershell.py.
if [ -f "$ROOT/lib/libLayerShellQtInterface.so.6" ]; then
  cp "$ROOT/lib/libLayerShellQtInterface.so.6" "$APPDIR/usr/lib/"
  echo "  libLayerShellQtInterface.so.6 copiada de lib/"
elif [ -f "/usr/lib64/libLayerShellQtInterface.so.6" ]; then
  cp "/usr/lib64/libLayerShellQtInterface.so.6" "$APPDIR/usr/lib/"
  echo "  libLayerShellQtInterface.so.6 copiada de /usr/lib64/"
else
  echo "  AVISO: libLayerShellQtInterface.so.6 não encontrada — layer-shell pode falhar"
fi

# liblayer-shell.so: plugin Qt Wayland que ativa o protocolo wlr-layer-shell.
# O pip PyQt6 não o inclui; copiamos do sistema para o dir de plugins do venv.
WSI_SYSTEM="/usr/lib64/qt6/plugins/wayland-shell-integration/liblayer-shell.so"
WSI_DIR="$PYQT6_PLUGINS/wayland-shell-integration"
if [ -f "$WSI_SYSTEM" ] && [ -d "$WSI_DIR" ]; then
  cp "$WSI_SYSTEM" "$WSI_DIR/"
  echo "  liblayer-shell.so copiada para plugins do venv"
else
  echo "  AVISO: liblayer-shell.so não copiada (sistema: $([ -f "$WSI_SYSTEM" ] && echo ok || echo ausente), dir: $([ -d "$WSI_DIR" ] && echo ok || echo ausente))"
fi

# ── 6. Wrapper executável ─────────────────────────────────────────────
cat > "$APPDIR/usr/bin/epicpen" << 'WRAPPER'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "$SELF_DIR/../lib/epicpen-venv/bin/python3" "$SELF_DIR/main.py" "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/epicpen"

# ── 7. AppRun — define env idêntico ao run.sh ─────────────────────────
# LD_LIBRARY_PATH: usr/lib/ (layershell so) + Qt6/lib (bundled Qt para que
# libLayerShellQtInterface use a mesma instância Qt do processo).
# Qt6/lib path calculado dinamicamente em runtime para funcionar em qualquer máquina.
PYQT6_LIBS_REL="${PYQT6_LIBS#${APPDIR}/}"
cat > "$APPDIR/AppRun" << APPRUN
#!/usr/bin/env bash
HERE="\$(dirname "\$(readlink -f "\$0")")"
export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_ACCESSIBILITY=0
export LD_LIBRARY_PATH="\${HERE}/usr/lib:\${HERE}/${PYQT6_LIBS_REL}\${LD_LIBRARY_PATH:+:\${LD_LIBRARY_PATH}}"
exec "\${HERE}/usr/bin/epicpen" "\$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# ── 8. Desktop entry + ícone ──────────────────────────────────────────
echo "→ Copiando .desktop e ícone..."
cp "$ROOT/epicpen.desktop" "$APPDIR/"
ICON_SRC="$ROOT/resources/icons/epicpen.png"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APPDIR/epicpen.png"
  cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/epicpen.png"
else
  echo "  AVISO: ícone não encontrado em $ICON_SRC"
fi

# ── 9. Empacota com appimagetool ──────────────────────────────────────
APPIMAGETOOL=""
for candidate in appimagetool "$ROOT/tools/appimagetool" "$ROOT/tools/appimagetool-${ARCH}.AppImage"; do
  if command -v "$candidate" &>/dev/null || [ -x "$candidate" ]; then
    APPIMAGETOOL="$candidate"
    break
  fi
done

if [ -n "$APPIMAGETOOL" ]; then
  echo "→ Gerando AppImage com $APPIMAGETOOL..."
  ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUTPUT"
  echo ""
  echo "✓ AppImage gerado: $(basename "$OUTPUT")"
  echo "  Tamanho: $(du -sh "$OUTPUT" | cut -f1)"
else
  echo ""
  echo "⚠  appimagetool não encontrado."
  echo "   Baixe em: https://github.com/AppImage/appimagetool/releases"
  echo "   Coloque em: $ROOT/tools/appimagetool"
  echo "   AppDir pronto em: $APPDIR"
  echo "   Execute manualmente:"
  echo "     appimagetool $APPDIR $OUTPUT"
fi
