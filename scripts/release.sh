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

# Python standalone (portátil, sem deps de sistema)
# venvs criados com o Python do sistema usam symlinks para o intérprete da
# máquina de build — não funcionam em outras distros. python-build-standalone
# inclui o intérprete + stdlib num único dir relocável.
PYTHON_CACHE="$ROOT/tools/python-standalone"
PYTHON_BIN="$PYTHON_CACHE/bin/python3"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "→ Python standalone não encontrado — a descarregar python-build-standalone..."
  mkdir -p "$ROOT/tools"

  ARCHIVE_URL=$(curl -fsSL "https://api.github.com/repos/indygreg/python-build-standalone/releases/latest" | \
    python3 -c "
import json, sys
data = json.load(sys.stdin)
for a in data.get('assets', []):
    n = a['name']
    if ('cpython-3.12' in n
            and 'x86_64-unknown-linux-gnu' in n
            and 'install_only_stripped' in n
            and n.endswith('.tar.gz')):
        print(a['browser_download_url'])
        break
" 2>/dev/null || true)

  if [ -z "$ARCHIVE_URL" ]; then
    echo -e "${RED}ERRO: não foi possível determinar URL do Python standalone.${RESET}"
    echo "  Aceda a https://github.com/indygreg/python-build-standalone/releases"
    echo "  e coloque o tarball extraído em: $PYTHON_CACHE"
    exit 1
  fi

  ARCHIVE="/tmp/epicpen-python-standalone.tar.gz"
  echo "  URL: $ARCHIVE_URL"
  curl -fL --progress-bar "$ARCHIVE_URL" -o "$ARCHIVE"
  mkdir -p "$PYTHON_CACHE"
  # O tarball extrai para python/ — strip-components=1 coloca direto em PYTHON_CACHE
  tar -xzf "$ARCHIVE" --strip-components=1 -C "$PYTHON_CACHE"
  rm -f "$ARCHIVE"
  echo -e "${GREEN}→ Python standalone instalado: $("$PYTHON_BIN" --version 2>&1)${RESET}"
else
  echo "→ Python standalone em cache ($("$PYTHON_BIN" --version 2>&1))."
fi

rm -rf "$APPDIR/usr/bin" "$APPDIR/usr/lib/python-standalone" "$APPDIR/usr/lib/epicpen-venv"
# Remove libs que possam ter ficado de builds anteriores
rm -f "$APPDIR/usr/lib/libstdc++.so.6" "$APPDIR/usr/lib/libgcc_s.so.1"
rm -f "$APPDIR/usr/lib"/libxcb*.so* "$APPDIR/usr/lib"/libxkbcommon*.so* "$APPDIR/usr/lib/libX11-xcb.so.1"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/lib" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

echo "→ Copiando Python standalone para AppDir..."
cp -r "$PYTHON_CACHE" "$APPDIR/usr/lib/python-standalone"
APPDIR_PYTHON="$APPDIR/usr/lib/python-standalone/bin/python3"

echo "→ Instalando dependências (PyQt6==6.10.1)..."
"$APPDIR_PYTHON" -m pip install --quiet "PyQt6==6.10.1"

echo "→ Gerando ícone..."
"$APPDIR_PYTHON" "$SCRIPT_DIR/generate_icon.py"

PYQT6_PLUGINS=$("$APPDIR_PYTHON" -c \
  "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins'))")

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

# Bundla libs xcb/X11 + C++ runtime (Ubuntu 22.04) — sem DT_RELR
# Fedora 43 usa binutils 2.41+ → DT_RELR → requer glibc 2.36 → quebra Ubuntu 22.04 (glibc 2.35).
# libstdc++/libgcc_s do Ubuntu 22.04 GCC 12: GLIBCXX_3.4.30, necessário para Ubuntu 20.04.
echo "→ Bundling libs xcb/X11 (Ubuntu 22.04 Jammy)..."

XCB_CACHE="$ROOT/tools/xcb-ubuntu"
mkdir -p "$XCB_CACHE"
[ -f "$ROOT/tools/libxcb-cursor.so.0" ] && \
  mv "$ROOT/tools/libxcb-cursor.so.0" "$XCB_CACHE/libxcb-cursor.so.0" 2>/dev/null || true

_extract_so_from_deb() {
  local deb="$1" prefix="$2" dest="$3"
  local dir
  dir=$(mktemp -d)
  (cd "$dir" && ar x "$deb" 2>/dev/null)
  local tf
  tf=$(ls "$dir"/data.tar.* 2>/dev/null | head -1)
  [ -n "$tf" ] && tar xf "$tf" -C "$dir" 2>/dev/null
  local so
  so=$(find "$dir" -name "${prefix}*.so*" -type f ! -name "*.py" | head -1)
  [ -n "$so" ] && cp "$so" "$dest"
  rm -rf "$dir"
  [ -n "$so" ]
}

_fetch_ubuntu_lib() {
  local pool_url="$1" deb_name="$2" so_name="$3"
  local cache_file="$XCB_CACHE/$so_name"
  if [ ! -f "$cache_file" ]; then
    local deb_tmp="/tmp/ubuntu-xcb-${deb_name}"
    if curl -fsSL --connect-timeout 30 "${pool_url}${deb_name}" -o "$deb_tmp" 2>/dev/null; then
      local prefix="${so_name%%.*}"
      if _extract_so_from_deb "$deb_tmp" "$prefix" "$cache_file"; then
        echo "  $so_name ← $deb_name"
      else
        echo -e "${YELLOW}  AVISO: $so_name não extraída de $deb_name${RESET}"
      fi
    else
      echo -e "${YELLOW}  AVISO: falha ao descarregar $deb_name${RESET}"
    fi
    rm -f "$deb_tmp"
  fi
  [ -f "$cache_file" ] && cp "$cache_file" "$APPDIR/usr/lib/$so_name"
}

POOL_LIBXCB="http://archive.ubuntu.com/ubuntu/pool/main/libx/libxcb/"
POOL_XCB_UTIL="http://archive.ubuntu.com/ubuntu/pool/main/x/xcb-util/"
POOL_XCB_WM="http://archive.ubuntu.com/ubuntu/pool/main/x/xcb-util-wm/"
POOL_XCB_IMAGE="http://archive.ubuntu.com/ubuntu/pool/main/x/xcb-util-image/"
POOL_XCB_KEYS="http://archive.ubuntu.com/ubuntu/pool/main/x/xcb-util-keysyms/"
POOL_XCB_RUTIL="http://archive.ubuntu.com/ubuntu/pool/main/x/xcb-util-renderutil/"
POOL_XKBCOMMON="http://archive.ubuntu.com/ubuntu/pool/main/libx/libxkbcommon/"
POOL_LIBX11="http://archive.ubuntu.com/ubuntu/pool/main/libx/libx11/"
POOL_XCB_CURSOR="http://archive.ubuntu.com/ubuntu/pool/universe/x/xcb-util-cursor/"

_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-shm0_1.14-3ubuntu3_amd64.deb"          "libxcb-shm.so.0"
_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-render0_1.14-3ubuntu3_amd64.deb"        "libxcb-render.so.0"
_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-shape0_1.14-3ubuntu3_amd64.deb"         "libxcb-shape.so.0"
_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-randr0_1.14-3ubuntu3_amd64.deb"         "libxcb-randr.so.0"
_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-sync1_1.14-3ubuntu3_amd64.deb"          "libxcb-sync.so.1"
_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-xfixes0_1.14-3ubuntu3_amd64.deb"        "libxcb-xfixes.so.0"
_fetch_ubuntu_lib "$POOL_LIBXCB"    "libxcb-xkb1_1.14-3ubuntu3_amd64.deb"           "libxcb-xkb.so.1"
_fetch_ubuntu_lib "$POOL_XCB_UTIL"  "libxcb-util1_0.4.0-0ubuntu3_amd64.deb"         "libxcb-util.so.1"
_fetch_ubuntu_lib "$POOL_XCB_WM"    "libxcb-icccm4_0.4.1-1.1_amd64.deb"            "libxcb-icccm.so.4"
_fetch_ubuntu_lib "$POOL_XCB_IMAGE" "libxcb-image0_0.4.0-2build1_amd64.deb"         "libxcb-image.so.0"
_fetch_ubuntu_lib "$POOL_XCB_KEYS"  "libxcb-keysyms1_0.4.0-1build1_amd64.deb"      "libxcb-keysyms.so.1"
_fetch_ubuntu_lib "$POOL_XCB_RUTIL" "libxcb-render-util0_0.3.9-1build1_amd64.deb"  "libxcb-render-util.so.0"
_fetch_ubuntu_lib "$POOL_XKBCOMMON" "libxkbcommon0_1.4.0-1_amd64.deb"              "libxkbcommon.so.0"
_fetch_ubuntu_lib "$POOL_XKBCOMMON" "libxkbcommon-x11-0_1.4.0-1_amd64.deb"          "libxkbcommon-x11.so.0"
_fetch_ubuntu_lib "$POOL_LIBX11"    "libx11-xcb1_1.7.5-1_amd64.deb"                "libX11-xcb.so.1"
_fetch_ubuntu_lib "$POOL_XCB_CURSOR" "libxcb-cursor0_0.1.1-3ubuntu1_amd64.deb"     "libxcb-cursor.so.0"

# C++ runtime — Ubuntu 22.04 GCC 12 (GLIBCXX_3.4.30, sem DT_RELR, glibc ≥ 2.17)
POOL_GCC12="http://archive.ubuntu.com/ubuntu/pool/main/g/gcc-12/"
_fetch_ubuntu_lib "$POOL_GCC12" "libstdc++6_12.3.0-1ubuntu1~22.04.3_amd64.deb" "libstdc++.so.6"
_fetch_ubuntu_lib "$POOL_GCC12" "libgcc-s1_12.3.0-1ubuntu1~22.04.3_amd64.deb"  "libgcc_s.so.1"

if [ -f "$XCB_CACHE/libxcb-cursor.so.0" ]; then
  PYQT6_QT6LIB=$("$APPDIR_PYTHON" -c \
    "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'lib'))")
  cp "$XCB_CACHE/libxcb-cursor.so.0" "$PYQT6_QT6LIB/libxcb-cursor.so.0"
  echo "  libxcb-cursor.so.0 → $(basename $PYQT6_QT6LIB)/ também (RUNPATH)"
else
  echo -e "${RED}  ERRO: libxcb-cursor.so.0 não disponível — X11 não vai funcionar${RESET}"
fi

cat > "$APPDIR/usr/bin/epicpen" << 'WRAPPER'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "$SELF_DIR/../lib/python-standalone/bin/python3" "$SELF_DIR/main.py" "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/epicpen"

cat > "$APPDIR/AppRun" << 'APPRUN'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_ACCESSIBILITY=0
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${QT_QPA_PLATFORM:-}" ]; then
  export QT_QPA_PLATFORM=wayland
fi
export LD_LIBRARY_PATH="${HERE}/usr/lib:${HERE}/usr/lib/python-standalone/lib/python3.12/site-packages/PyQt6/Qt6/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
if [ -f "${HERE}/usr/lib/libxcb-cursor.so.0" ]; then
  export LD_PRELOAD="${HERE}/usr/lib/libxcb-cursor.so.0${LD_PRELOAD:+:${LD_PRELOAD}}"
fi
exec "${HERE}/usr/bin/epicpen" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "→ Copiando .desktop e ícone..."
cp "$ROOT/epicpen.desktop" "$APPDIR/"
ICON_SRC="$ROOT/resources/icons/epicpen.png"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APPDIR/epicpen.png"
  cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/epicpen.png"
fi

# Arch/Manjaro têm apenas FUSE3 (fusermount3); o runtime padrão (continuous)
# usa FUSE2 e segfaulta. Passamos --runtime-file com o runtime fuse3 do release
# "old" (651680 bytes), que suporta FUSE2 e FUSE3.
# NOTA: appimagetool deve terminar sem pipe (SIGPIPE interrompe a gravação do ELF).
FUSE3_RUNTIME="$ROOT/tools/runtime-fuse3-x86_64"

ensure_fuse3_runtime() {
  if [ -f "$FUSE3_RUNTIME" ]; then
    local sz
    sz=$(wc -c < "$FUSE3_RUNTIME")
    if [ "$sz" -ge 600000 ]; then
      echo "→ runtime-fuse3 em cache ($sz bytes)."
      return
    fi
    echo "  runtime-fuse3 em cache parece inválido ($sz bytes) — removendo..."
    rm -f "$FUSE3_RUNTIME"
  fi
  echo "→ Descarregando runtime AppImage com suporte FUSE3..."
  local url="https://github.com/AppImage/type2-runtime/releases/download/old/runtime-fuse3-x86_64"
  if curl -fsSL --connect-timeout 60 "$url" -o "$FUSE3_RUNTIME" 2>/dev/null; then
    chmod +x "$FUSE3_RUNTIME"
    echo "  runtime-fuse3-x86_64 descarregado ($(wc -c < "$FUSE3_RUNTIME") bytes)"
  else
    rm -f "$FUSE3_RUNTIME"
    echo -e "${YELLOW}  AVISO: falha ao descarregar runtime-fuse3 — AppImage pode não funcionar no Arch/Manjaro${RESET}"
  fi
}

ensure_fuse3_runtime

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
RUNTIME_OPT=()
[ -f "$FUSE3_RUNTIME" ] && RUNTIME_OPT=(--runtime-file "$FUSE3_RUNTIME")
ARCH="$ARCH" "$APPIMAGETOOL" "${RUNTIME_OPT[@]}" "$APPDIR" "$OUTPUT"

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
