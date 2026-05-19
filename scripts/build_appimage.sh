#!/usr/bin/env bash
set -euo pipefail

# Build script para gerar o AppImage do EpicPen Linux.
# Requer: appimagetool, python3, pip

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
APPDIR="$PROJECT_ROOT/AppDir"
VERSION=$(git -C "$PROJECT_ROOT" describe --tags --abbrev=0 2>/dev/null || echo "0.1.0")

echo "==> Buildando EpicPen $VERSION"

# 1. Cria venv e instala dependências dentro do AppDir
PYTHON_VENV="$APPDIR/usr/lib/epicpen-venv"
python3 -m venv "$PYTHON_VENV"
"$PYTHON_VENV/bin/pip" install --quiet PyQt6

# 2. Copia os fontes
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications"
cp -r "$PROJECT_ROOT/src/"* "$APPDIR/usr/bin/"

# 3. Wrapper executável
cat > "$APPDIR/usr/bin/epicpen" << 'EOF'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "$SELF_DIR/../lib/epicpen-venv/bin/python3" "$SELF_DIR/main.py" "$@"
EOF
chmod +x "$APPDIR/usr/bin/epicpen"

# 4. AppRun
cat > "$APPDIR/AppRun" << 'EOF'
#!/usr/bin/env bash
exec "$(dirname "$0")/usr/bin/epicpen" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# 5. Desktop e ícone
cp "$PROJECT_ROOT/epicpen.desktop" "$APPDIR/"
# Ícone placeholder — substituir por PNG 256x256 real
if [ -f "$PROJECT_ROOT/resources/icons/epicpen.png" ]; then
    cp "$PROJECT_ROOT/resources/icons/epicpen.png" \
       "$APPDIR/usr/share/icons/hicolor/256x256/apps/epicpen.png"
    cp "$PROJECT_ROOT/resources/icons/epicpen.png" "$APPDIR/epicpen.png"
fi

# 6. Gera AppImage
if command -v appimagetool &>/dev/null; then
    appimagetool "$APPDIR" "$PROJECT_ROOT/EpicPen-$VERSION-x86_64.AppImage"
    echo "==> AppImage gerado: EpicPen-$VERSION-x86_64.AppImage"
else
    echo "AVISO: appimagetool não encontrado. Baixe em:"
    echo "  https://github.com/AppImage/appimagetool/releases"
    echo "O AppDir está pronto em: $APPDIR"
fi
