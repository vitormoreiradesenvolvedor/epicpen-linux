#!/usr/bin/env bash
# Gerador interactivo de AppImage para o EpicPen Linux.
# Pergunta o tipo de versão, sugere a próxima versão natural e constrói.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
ARCH=$(uname -m)

# ── Cores ─────────────────────────────────────────────────────────────────────
BOLD='\033[1m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║     EpicPen Linux — Gerador de Release   ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# ── 1. Tipo: dev ou oficial ────────────────────────────────────────────────────
echo -e "${CYAN}Tipo de build:${RESET}"
echo "  1) Versão oficial  (cria tag git, AppImage sem sufixo)"
echo "  2) Versão de dev   (sem tag, AppImage com sufixo -dev)"
echo ""
while true; do
    read -rp "Escolha [1/2]: " BUILD_TYPE
    case "$BUILD_TYPE" in
        1) IS_DEV=false; break ;;
        2) IS_DEV=true;  break ;;
        *) echo -e "${RED}  Digite 1 ou 2.${RESET}" ;;
    esac
done

# ── 2. Calcula próxima versão natural ─────────────────────────────────────────
LAST_TAG=$(git -C "$ROOT" tag --sort=-v:refname 2>/dev/null | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)

if [ -z "$LAST_TAG" ]; then
    SUGGESTED="1.0.0"
else
    # Remove prefixo "v" se existir
    CLEAN="${LAST_TAG#v}"
    MAJOR=$(echo "$CLEAN" | cut -d. -f1)
    MINOR=$(echo "$CLEAN" | cut -d. -f2)
    PATCH=$(echo "$CLEAN" | cut -d. -f3)
    SUGGESTED="${MAJOR}.${MINOR}.$((PATCH + 1))"
fi

echo ""
if [ -z "$LAST_TAG" ]; then
    echo -e "${YELLOW}Nenhuma tag encontrada.${RESET}"
else
    echo -e "Última versão tag: ${BOLD}${LAST_TAG}${RESET}"
fi
echo -e "Próxima versão sugerida: ${BOLD}${SUGGESTED}${RESET}"
echo ""
echo "  1) Confirmar ${SUGGESTED}"
echo "  2) Inserir versão personalizada"
echo ""
while true; do
    read -rp "Escolha [1/2]: " VER_CHOICE
    case "$VER_CHOICE" in
        1)
            VERSION="$SUGGESTED"
            break
            ;;
        2)
            while true; do
                read -rp "Versão (ex: 1.2.0): " CUSTOM_VER
                if echo "$CUSTOM_VER" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
                    VERSION="$CUSTOM_VER"
                    break
                else
                    echo -e "${RED}  Formato inválido. Use X.Y.Z (ex: 1.2.0)${RESET}"
                fi
            done
            break
            ;;
        *) echo -e "${RED}  Digite 1 ou 2.${RESET}" ;;
    esac
done

# ── 3. Sufixo e nome final ─────────────────────────────────────────────────────
if $IS_DEV; then
    FULL_VERSION="${VERSION}-dev"
    TAG_LABEL=""
else
    FULL_VERSION="${VERSION}"
    TAG_LABEL="v${VERSION}"
fi

OUTPUT_NAME="EpicPen-${FULL_VERSION}-${ARCH}.AppImage"
OUTPUT="$ROOT/$OUTPUT_NAME"

# ── 4. Confirmação final ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Resumo:${RESET}"
echo -e "  Versão   : ${BOLD}${FULL_VERSION}${RESET}"
echo -e "  Ficheiro : ${BOLD}${OUTPUT_NAME}${RESET}"
if $IS_DEV; then
    echo -e "  Tag git  : ${YELLOW}não criada (build de dev)${RESET}"
else
    echo -e "  Tag git  : ${GREEN}v${VERSION} será criada${RESET}"
fi
echo ""
read -rp "Confirmar e gerar AppImage? [s/N]: " CONFIRM
case "$CONFIRM" in
    s|S|y|Y) ;;
    *) echo "Cancelado."; exit 0 ;;
esac

# ── 5. Tag git (apenas versão oficial) ────────────────────────────────────────
if ! $IS_DEV; then
    if git -C "$ROOT" tag | grep -qx "v${VERSION}"; then
        echo -e "${YELLOW}Tag v${VERSION} já existe — reutilizando.${RESET}"
    else
        git -C "$ROOT" tag -a "v${VERSION}" -m "Release v${VERSION}"
        echo -e "${GREEN}Tag v${VERSION} criada.${RESET}"
    fi
fi

# ── 6. Build AppImage ─────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}A construir AppImage...${RESET}"
echo ""

APPDIR="$ROOT/AppDir"
rm -rf "$APPDIR/usr/bin" "$APPDIR/usr/lib/epicpen-venv"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/lib" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

echo "→ Gerando ícone..."
python3 "$SCRIPT_DIR/generate_icon.py"

echo "→ Instalando dependências no venv (PyQt6==6.10.1)..."
VENV="$APPDIR/usr/lib/epicpen-venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet "PyQt6==6.10.1"

PYQT6_PLUGINS=$("$VENV/bin/python" -c \
  "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins'))")
PYQT6_LIBS=$("$VENV/bin/python" -c \
  "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'lib'))")

echo "→ Copiando fontes e recursos..."
cp -r "$ROOT/src/"* "$APPDIR/usr/bin/"
cp -r "$ROOT/resources" "$APPDIR/usr/resources"

echo "→ Copiando libs layer-shell..."
if [ -f "$ROOT/lib/libLayerShellQtInterface.so.6" ]; then
  cp "$ROOT/lib/libLayerShellQtInterface.so.6" "$APPDIR/usr/lib/"
elif [ -f "/usr/lib64/libLayerShellQtInterface.so.6" ]; then
  cp "/usr/lib64/libLayerShellQtInterface.so.6" "$APPDIR/usr/lib/"
else
  echo -e "${YELLOW}  AVISO: libLayerShellQtInterface.so.6 não encontrada${RESET}"
fi

WSI_SYSTEM="/usr/lib64/qt6/plugins/wayland-shell-integration/liblayer-shell.so"
WSI_DIR="$PYQT6_PLUGINS/wayland-shell-integration"
if [ -f "$WSI_SYSTEM" ] && [ -d "$WSI_DIR" ]; then
  cp "$WSI_SYSTEM" "$WSI_DIR/"
fi

cat > "$APPDIR/usr/bin/epicpen" << 'WRAPPER'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "$SELF_DIR/../lib/epicpen-venv/bin/python3" "$SELF_DIR/main.py" "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/epicpen"

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

echo "→ Copiando .desktop e ícone..."
cp "$ROOT/epicpen.desktop" "$APPDIR/"
ICON_SRC="$ROOT/resources/icons/epicpen.png"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APPDIR/epicpen.png"
  cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/epicpen.png"
fi

APPIMAGETOOL=""
for candidate in appimagetool "$ROOT/tools/appimagetool" "$ROOT/tools/appimagetool-${ARCH}.AppImage"; do
  if command -v "$candidate" &>/dev/null || [ -x "$candidate" ]; then
    APPIMAGETOOL="$candidate"
    break
  fi
done

if [ -z "$APPIMAGETOOL" ]; then
    echo ""
    echo -e "${RED}⚠  appimagetool não encontrado.${RESET}"
    echo "   Baixe em: https://github.com/AppImage/appimagetool/releases"
    echo "   Coloque em: $ROOT/tools/appimagetool"
    exit 1
fi

echo "→ Empacotando com appimagetool..."
ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUTPUT" 2>&1 \
  | grep -v "^$" | grep -v "^Parallel" | grep -v "^Creating" \
  | grep -v "^Exportable" | grep -v "^\s" | grep -v "^Number" \
  | grep -v "^Inode\|^Direct\|^Xattr\|^Filesystem\|^Embedding\|^Marking\|^Success\|^Please\|^  " \
  || true

# ── 7. Resultado ──────────────────────────────────────────────────────────────
echo ""
if [ -f "$OUTPUT" ]; then
    SIZE=$(du -sh "$OUTPUT" | cut -f1)
    echo -e "${GREEN}${BOLD}✓ AppImage gerado com sucesso!${RESET}"
    echo -e "  Ficheiro : ${BOLD}${OUTPUT_NAME}${RESET}"
    echo -e "  Tamanho  : ${BOLD}${SIZE}${RESET}"
    echo -e "  Versão   : ${BOLD}${FULL_VERSION}${RESET}"
    if ! $IS_DEV; then
        echo -e "  Tag git  : ${BOLD}v${VERSION}${RESET}"
    fi
else
    echo -e "${RED}✗ Falha ao gerar AppImage.${RESET}"
    exit 1
fi
echo ""
