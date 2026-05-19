#!/usr/bin/env bash
# Build script para gerar o AppImage do EpicPen Linux.
# Uso: bash scripts/build_appimage.sh
# Requer: python3 + PyQt6 instalados; appimagetool no PATH ou em ./tools/

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
echo "→ Instalando dependências no venv..."
VENV="$APPDIR/usr/lib/epicpen-venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet PyQt6

# ── 4. Copia fontes ────────────────────────────────────────────────────
echo "→ Copiando fontes..."
cp -r "$ROOT/src/"* "$APPDIR/usr/bin/"

# ── 5. Wrapper executável ─────────────────────────────────────────────
cat > "$APPDIR/usr/bin/epicpen" << 'WRAPPER'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "$SELF_DIR/../lib/epicpen-venv/bin/python3" "$SELF_DIR/main.py" "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/epicpen"

# ── 6. AppRun ─────────────────────────────────────────────────────────
cat > "$APPDIR/AppRun" << 'APPRUN'
#!/usr/bin/env bash
exec "$(dirname "$0")/usr/bin/epicpen" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# ── 7. Desktop entry + ícone ──────────────────────────────────────────
echo "→ Copiando .desktop e ícone..."
cp "$ROOT/epicpen.desktop" "$APPDIR/"
ICON_SRC="$ROOT/resources/icons/epicpen.png"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APPDIR/epicpen.png"
  cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/epicpen.png"
else
  echo "  AVISO: ícone não encontrado em $ICON_SRC"
fi

# ── 8. Empacota com appimagetool ──────────────────────────────────────
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
