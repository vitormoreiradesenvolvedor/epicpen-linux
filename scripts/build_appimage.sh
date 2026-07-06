#!/usr/bin/env bash
# Build script para gerar o AppImage do EpicPen Linux.
# Uso: bash scripts/build_appimage.sh
# Requer: curl, tar; appimagetool no PATH ou em ./tools/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
APPDIR="$ROOT/AppDir"
VERSION=$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null || echo "0.1.0-dev")
ARCH=$(uname -m)
OUTPUT="$ROOT/EpicPen-${VERSION}-${ARCH}.AppImage"

# Cache do ffmpeg GPL compilado (libx264) — reutilizado entre builds
FFMPEG_GPL_CACHE="$ROOT/tools/ffmpeg-gpl"
FFMPEG_GPL_BIN="$FFMPEG_GPL_CACHE/ffmpeg"

# Cache do grim compilado — captura de região para a lupa/screenshot em
# compositores wlroots (Hyprland, Sway...) sem depender do sistema
GRIM_VERSION="1.5.0"
GRIM_CACHE="$ROOT/tools/grim"
GRIM_BIN="$GRIM_CACHE/grim"

echo "══════════════════════════════════════════"
echo "  EpicPen Linux — Build AppImage v${VERSION}"
echo "══════════════════════════════════════════"

# ── 0. Python standalone (portátil, sem deps de sistema) ──────────────────
# venvs criados com o Python do sistema usam symlinks para o intérprete da
# máquina de build — não funcionam em outras distros. python-build-standalone
# inclui o intérprete + stdlib num único dir relocável.
PYTHON_CACHE="$ROOT/tools/python-standalone"
PYTHON_BIN="$PYTHON_CACHE/bin/python3"

ensure_standalone_python() {
  if [ -x "$PYTHON_BIN" ]; then
    echo "→ Python standalone em cache ($("$PYTHON_BIN" --version 2>&1))."
    return
  fi

  echo "→ Python standalone não encontrado — a descarregar python-build-standalone..."
  mkdir -p "$ROOT/tools"

  local ARCHIVE_URL
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
    echo "ERRO: não foi possível determinar URL do Python standalone."
    echo "  Aceda a https://github.com/indygreg/python-build-standalone/releases"
    echo "  e descarregue manualmente:"
    echo "    cpython-3.12.*-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
    echo "  Extraia com --strip-components=1 para: $PYTHON_CACHE"
    exit 1
  fi

  echo "  URL: $ARCHIVE_URL"
  local ARCHIVE="/tmp/epicpen-python-standalone.tar.gz"
  curl -fL --progress-bar "$ARCHIVE_URL" -o "$ARCHIVE"
  mkdir -p "$PYTHON_CACHE"
  # O tarball extrai para python/ — strip-components=1 coloca diretamente em PYTHON_CACHE
  tar -xzf "$ARCHIVE" --strip-components=1 -C "$PYTHON_CACHE"
  rm -f "$ARCHIVE"
  echo "→ Python standalone instalado: $("$PYTHON_BIN" --version 2>&1)"
}

ensure_standalone_python

# ── 0b. Compila ffmpeg GPL + libx264 para screen recording ───────────────────
# Produz um binário estático (~20-30 MB) cacheado em tools/ffmpeg-gpl/.
# Depende de: nasm gcc g++ make pkg-config (instalados no CI via apt-get).
# Em caso de falha avisa e continua; o recorder usará o ffmpeg do sistema.
build_recorder_ffmpeg() {
  if [ -x "$FFMPEG_GPL_BIN" ]; then
    echo "→ ffmpeg GPL em cache ($(du -sh "$FFMPEG_GPL_BIN" | cut -f1))."
    cp "$FFMPEG_GPL_BIN" "$APPDIR/usr/bin/ffmpeg"
    return
  fi

  for dep in nasm gcc g++ make pkg-config; do
    if ! command -v "$dep" &>/dev/null; then
      echo "  AVISO: '$dep' não encontrado — ffmpeg GPL não será bundlado."
      echo "         Screen recording usará o ffmpeg do sistema (se disponível)."
      return
    fi
  done

  echo "→ Compilando ffmpeg GPL + libx264 para screen recording..."
  echo "  (resultado cacheado em tools/ffmpeg-gpl — próximas builds serão instantâneas)"

  local BUILD_TMP
  BUILD_TMP=$(mktemp -d -t epicpen-ffmpeg-XXXXXX)
  local PREFIX="$BUILD_TMP/prefix"
  mkdir -p "$PREFIX"

  if ! (
    set -euo pipefail

    # ── libx264 — estático, 8-bit, com PIC, sem CLI ──────────────────────────
    echo "  [1/2] libx264..."
    git clone --depth 1 -q \
      "https://code.videolan.org/videolan/x264.git" "$BUILD_TMP/x264"
    cd "$BUILD_TMP/x264"
    ./configure \
      --prefix="$PREFIX" \
      --enable-static \
      --enable-pic \
      --bit-depth=8 \
      --disable-cli \
      --disable-opencl \
      --extra-cflags="-O3 -fPIC" \
      > /dev/null 2>&1
    make -j"$(nproc)" > /dev/null 2>&1
    make install > /dev/null 2>&1

    # ── ffmpeg mínimo — só o que o screen recorder precisa ───────────────────
    echo "  [2/2] ffmpeg (≈5 min na primeira vez)..."
    git clone --depth 1 -q \
      --branch release/7.1 \
      "https://github.com/FFmpeg/FFmpeg.git" "$BUILD_TMP/ffmpeg"
    cd "$BUILD_TMP/ffmpeg"
    PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" \
    ./configure \
      --prefix="$PREFIX" \
      --enable-gpl \
      --enable-libx264 \
      --disable-everything \
      --enable-avcodec \
      --enable-avformat \
      --enable-avutil \
      --enable-swscale \
      --enable-swresample \
      --enable-avfilter \
      --enable-encoder=libx264 \
      --enable-decoder=rawvideo \
      --enable-demuxer=rawvideo \
      --enable-muxer=mp4,mov \
      --enable-protocol=file,pipe \
      --enable-filter=buffer,buffersink,scale,format,null \
      --disable-shared \
      --enable-static \
      --disable-doc \
      --disable-ffplay \
      --disable-ffprobe \
      --pkg-config-flags="--static" \
      --extra-cflags="-O2 -I$PREFIX/include" \
      --extra-ldflags="-L$PREFIX/lib" \
      > /dev/null 2>&1
    make -j"$(nproc)" ffmpeg > /dev/null 2>&1
    strip --strip-unneeded ffmpeg
    mkdir -p "$(dirname "$FFMPEG_GPL_BIN")"
    cp ffmpeg "$FFMPEG_GPL_BIN"
    chmod +x "$FFMPEG_GPL_BIN"
  ); then
    echo "  AVISO: compilação do ffmpeg GPL falhou."
    echo "         Screen recording usará o ffmpeg do sistema (se disponível)."
    rm -rf "$BUILD_TMP" 2>/dev/null || true
    return
  fi

  rm -rf "$BUILD_TMP"
  echo "  → ffmpeg GPL pronto: $(du -sh "$FFMPEG_GPL_BIN" | cut -f1)"
  cp "$FFMPEG_GPL_BIN" "$APPDIR/usr/bin/ffmpeg"
}

# ── 0c. Compila grim para captura de região (lupa) em wlroots ────────────────
# Binário pequeno (~50 KB) cacheado em tools/grim/. O grim só funciona em
# compositores com wlr-screencopy/ext-image-copy-capture (Hyprland, Sway...);
# no KDE a captura silenciosa é via KWin ScreenShot2 (kwinshot.py) e no GNOME
# via portal (livegrab.py). Em caso de falha avisa e continua.
build_bundled_grim() {
  if [ -x "$GRIM_BIN" ]; then
    echo "→ grim em cache ($(du -sh "$GRIM_BIN" | cut -f1))."
    cp "$GRIM_BIN" "$APPDIR/usr/bin/grim"
    return
  fi

  echo "→ Compilando grim ${GRIM_VERSION} (captura de região wlroots)..."

  for dep in gcc pkg-config git curl; do
    if ! command -v "$dep" &>/dev/null; then
      echo "  AVISO: '$dep' não encontrado — grim não será bundlado."
      return
    fi
  done
  for pc in wayland-client wayland-scanner pixman-1 libpng; do
    if ! pkg-config --exists "$pc" 2>/dev/null; then
      echo "  AVISO: falta o pacote dev de '$pc' — grim não será bundlado."
      return
    fi
  done

  # meson + ninja: usa os do sistema; senão venv cacheado com o Python standalone
  local MESON_DIR=""
  if ! command -v meson &>/dev/null || ! command -v ninja &>/dev/null; then
    local MVENV="$ROOT/tools/meson-venv"
    if [ ! -x "$MVENV/bin/meson" ]; then
      echo "  meson/ninja ausentes — a instalar em tools/meson-venv..."
      "$PYTHON_BIN" -m venv "$MVENV"
      "$MVENV/bin/pip" install --quiet meson ninja
    fi
    MESON_DIR="$MVENV/bin"
  fi

  # wayland-protocols >= 1.37 (Ubuntu 22.04 traz 1.25) — vendorizado em tools/
  local WP_PKGCONFIG=""
  if ! pkg-config --atleast-version=1.37 wayland-protocols 2>/dev/null; then
    local WP_VERSION="1.45"
    local WP_PREFIX="$ROOT/tools/wayland-protocols"
    if [ ! -f "$WP_PREFIX/share/pkgconfig/wayland-protocols.pc" ]; then
      echo "  wayland-protocols do sistema < 1.37 — a vendorizar ${WP_VERSION}..."
      local WP_TMP
      WP_TMP=$(mktemp -d -t epicpen-wp-XXXXXX)
      if ! (
        set -euo pipefail
        curl -fsSL --connect-timeout 30 \
          "https://gitlab.freedesktop.org/wayland/wayland-protocols/-/releases/${WP_VERSION}/downloads/wayland-protocols-${WP_VERSION}.tar.xz" \
          -o "$WP_TMP/wp.tar.xz"
        tar -xf "$WP_TMP/wp.tar.xz" -C "$WP_TMP"
        cd "$WP_TMP/wayland-protocols-${WP_VERSION}"
        PATH="${MESON_DIR:+$MESON_DIR:}$PATH" \
          meson setup build --prefix="$WP_PREFIX" -Dtests=false > /dev/null
        PATH="${MESON_DIR:+$MESON_DIR:}$PATH" \
          meson install -C build > /dev/null
      ); then
        echo "  AVISO: vendorização do wayland-protocols falhou — grim não será bundlado."
        rm -rf "$WP_TMP"
        return
      fi
      rm -rf "$WP_TMP"
    fi
    WP_PKGCONFIG="$WP_PREFIX/share/pkgconfig"
  fi

  local BUILD_TMP
  BUILD_TMP=$(mktemp -d -t epicpen-grim-XXXXXX)
  if ! (
    set -euo pipefail
    git clone --depth 1 -q --branch "v${GRIM_VERSION}" \
      "https://gitlab.freedesktop.org/emersion/grim.git" "$BUILD_TMP/grim"
    cd "$BUILD_TMP/grim"
    PATH="${MESON_DIR:+$MESON_DIR:}$PATH" \
    PKG_CONFIG_PATH="${WP_PKGCONFIG}${WP_PKGCONFIG:+:}${PKG_CONFIG_PATH:-}" \
      meson setup build --buildtype=release -Djpeg=disabled -Dwerror=false \
      > /dev/null
    PATH="${MESON_DIR:+$MESON_DIR:}$PATH" ninja -C build > /dev/null
    strip --strip-unneeded build/grim
    mkdir -p "$GRIM_CACHE"
    cp build/grim "$GRIM_BIN"
    chmod +x "$GRIM_BIN"
  ); then
    echo "  AVISO: compilação do grim falhou — captura usará ferramentas do sistema."
    rm -rf "$BUILD_TMP"
    return
  fi
  rm -rf "$BUILD_TMP"
  cp "$GRIM_BIN" "$APPDIR/usr/bin/grim"
  echo "  → grim bundlado: $(du -sh "$GRIM_BIN" | cut -f1)"
}

# ── 1. Gera ícone PNG ──────────────────────────────────────────────────
echo "→ Gerando ícone..."
python3 "$SCRIPT_DIR/generate_icon.py"

# ── 2. Prepara AppDir ─────────────────────────────────────────────────
echo "→ Preparando AppDir..."
rm -rf "$APPDIR/usr/bin" "$APPDIR/usr/lib/python-standalone" "$APPDIR/usr/lib/epicpen-venv"
# Remove libs que possam ter ficado de builds anteriores
rm -f "$APPDIR/usr/lib/libstdc++.so.6" "$APPDIR/usr/lib/libgcc_s.so.1"
rm -f "$APPDIR/usr/lib"/libxcb*.so* "$APPDIR/usr/lib"/libxkbcommon*.so* "$APPDIR/usr/lib/libX11-xcb.so.1"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/lib" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# ── 2b. ffmpeg GPL (libx264) para screen recording ───────────────────
build_recorder_ffmpeg

# ── 2c. grim para captura de região (lupa) em wlroots ────────────────
build_bundled_grim

# ── 3. Copia Python standalone para AppDir e instala PyQt6 ────────────
# PyQt6==6.10.1 → Qt 6.10.0 bundled: mesma série minor da lib sistema (6.10.x)
# garantindo compatibilidade ABI com libLayerShellQtInterface compilada para Qt 6.10.
echo "→ Copiando Python standalone para AppDir..."
cp -r "$PYTHON_CACHE" "$APPDIR/usr/lib/python-standalone"
APPDIR_PYTHON="$APPDIR/usr/lib/python-standalone/bin/python3"

echo "→ Instalando PyQt6==6.10.1..."
"$APPDIR_PYTHON" -m pip install --quiet "PyQt6==6.10.1"
echo "  Qt version: $("$APPDIR_PYTHON" -c 'from PyQt6.QtCore import QT_VERSION_STR; print(QT_VERSION_STR)')"

# Descobre caminho dos plugins do PyQt6 bundled
PYQT6_PLUGINS=$("$APPDIR_PYTHON" -c \
  "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins'))")

# ── 4. Copia fontes e recursos ────────────────────────────────────────
echo "→ Copiando fontes e recursos..."
cp -r "$ROOT/src/"* "$APPDIR/usr/bin/"
# tray.py e icons.py usam Path(__file__).parent.parent / "resources"
# → no AppImage isso resolve para usr/resources/
cp -r "$ROOT/resources" "$APPDIR/usr/resources"

# ── 5. Copia libs nativas do layer-shell ──────────────────────────────
# libstdc++ e libgcc_s são bundladas a partir do Ubuntu 22.04 (GCC 12, sem DT_RELR).
# Ubuntu 20.04 tem GCC 9 → max GLIBCXX_3.4.28; Qt 6.10 requer 3.4.29+.
# Ubuntu 22.04 GCC 12 fornece GLIBCXX_3.4.30 e apenas precisa glibc ≥ 2.17.
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
# O pip PyQt6 não o inclui; copiamos do sistema para o dir de plugins do Python bundled.
WSI_SYSTEM="/usr/lib64/qt6/plugins/wayland-shell-integration/liblayer-shell.so"
WSI_DIR="$PYQT6_PLUGINS/wayland-shell-integration"
if [ -f "$WSI_SYSTEM" ] && [ -d "$WSI_DIR" ]; then
  cp "$WSI_SYSTEM" "$WSI_DIR/"
  echo "  liblayer-shell.so copiada para plugins do Python bundled"
else
  echo "  AVISO: liblayer-shell.so não copiada (sistema: $([ -f "$WSI_SYSTEM" ] && echo ok || echo ausente), dir: $([ -d "$WSI_DIR" ] && echo ok || echo ausente))"
fi

# ── 5b. Bundla libs xcb/X11 (Ubuntu 22.04 Jammy) ────────────────────────────
# Fedora 43 compila libs xcb com binutils 2.41+ → DT_RELR → requer glibc 2.36.
# Ubuntu 22.04 tem glibc 2.35 → incompatível. Descarregamos do Ubuntu 22.04 (sem DT_RELR).
echo "→ Bundling libs xcb/X11 (Ubuntu 22.04 Jammy)..."

XCB_CACHE="$ROOT/tools/xcb-ubuntu"
mkdir -p "$XCB_CACHE"
# Migrar cache antigo de libxcb-cursor
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
        echo "  AVISO: $so_name não extraída de $deb_name"
      fi
    else
      echo "  AVISO: falha ao descarregar $deb_name"
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

# libxcb-cursor também em PyQt6/Qt6/lib/ (RUNPATH de libqxcb.so = $ORIGIN/../../lib)
if [ -f "$XCB_CACHE/libxcb-cursor.so.0" ]; then
  PYQT6_QT6LIB=$("$APPDIR_PYTHON" -c \
    "import PyQt6, os; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'lib'))")
  cp "$XCB_CACHE/libxcb-cursor.so.0" "$PYQT6_QT6LIB/libxcb-cursor.so.0"
  echo "  libxcb-cursor.so.0 → $(basename $PYQT6_QT6LIB)/ também (RUNPATH)"
fi

# ── 6. Wrapper executável ─────────────────────────────────────────────
cat > "$APPDIR/usr/bin/epicpen" << 'WRAPPER'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "$SELF_DIR/../lib/python-standalone/bin/python3" "$SELF_DIR/main.py" "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/epicpen"

# ── 7. AppRun ─────────────────────────────────────────────────────────
# Wayland: forçar QT_QPA_PLATFORM=wayland → xcb nunca tentado.
# X11: todas as libs xcb bundladas em usr/lib/ — sem dependências de sistema.
cat > "$APPDIR/AppRun" << 'APPRUN'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_ACCESSIBILITY=0
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${QT_QPA_PLATFORM:-}" ]; then
  export QT_QPA_PLATFORM=wayland
fi
export LD_LIBRARY_PATH="${HERE}/usr/lib:${HERE}/usr/lib/python-standalone/lib/python3.12/site-packages/PyQt6/Qt6/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
# LD_PRELOAD garante que libxcb-cursor.so.0 está em memória antes de qualquer dlopen do Qt.
# LD_LIBRARY_PATH por si só pode ser ignorado quando o Qt faz dlopen() de plugins internamente.
if [ -f "${HERE}/usr/lib/libxcb-cursor.so.0" ]; then
  export LD_PRELOAD="${HERE}/usr/lib/libxcb-cursor.so.0${LD_PRELOAD:+:${LD_PRELOAD}}"
fi
exec "${HERE}/usr/bin/epicpen" "$@"
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

# ── 9. Empacota com appimagetool + runtime FUSE3 ──────────────────────
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
    echo "  AVISO: falha ao descarregar runtime-fuse3 — AppImage pode não funcionar no Arch/Manjaro"
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

if [ -n "$APPIMAGETOOL" ]; then
  echo "→ Gerando AppImage com $APPIMAGETOOL..."
  RUNTIME_OPT=()
  [ -f "$FUSE3_RUNTIME" ] && RUNTIME_OPT=(--runtime-file "$FUSE3_RUNTIME")
  ARCH="$ARCH" "$APPIMAGETOOL" "${RUNTIME_OPT[@]}" "$APPDIR" "$OUTPUT"
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
