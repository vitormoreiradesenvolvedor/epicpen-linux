import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QRect
from PyQt6.QtGui import QClipboard, QPixmap

_SAVE_DIR = Path.home() / "Imagens" / "EpicPen"

_IS_WAYLAND = (
    os.environ.get("WAYLAND_DISPLAY") is not None
    and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"
)

# Caminhos extras buscados quando o AppImage tem PATH restrito
_EXTRA_PATHS = [
    "/usr/bin", "/usr/local/bin", "/bin",
    "/usr/sbin", "/sbin",
    str(Path.home() / ".local" / "bin"),
    "/snap/bin",
    "/usr/games",
    "/usr/local/games",
    "/opt/bin",
]


def _which(tool: str) -> str | None:
    """Localiza o executável no PATH e em diretórios fixos do sistema."""
    found = shutil.which(tool)
    if found:
        return found
    for p in _EXTRA_PATHS:
        candidate = os.path.join(p, tool)
        if os.access(candidate, os.X_OK):
            return candidate
    return None


# ── Ferramentas disponíveis ───────────────────────────────────────────────────

def _available_tools() -> list[str]:
    known = [
        "grim", "wayshot", "hyprshot",
        "gnome-screenshot", "spectacle", "flameshot",
        "mate-screenshot", "xfce4-screenshooter",
        "maim", "scrot",
    ]
    return [t for t in known if _which(t)]


# ── Execução de ferramentas ───────────────────────────────────────────────────

def _try_tool(cmd: list[str], path: str, timeout: int = 10) -> bool:
    """Executa a ferramenta e verifica se produziu um arquivo válido."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0 and Path(path).exists() and Path(path).stat().st_size > 0
    except Exception:
        return False


def _grab_wayland_fullscreen(path: str) -> bool:
    """Tenta capturar toda a tela no Wayland. Salva em `path`. Retorna True se ok."""
    candidates: list[list[str]] = [
        # grim — wlroots (Hyprland, Sway, etc.)
        ["grim", path],
        # wayshot — alternativa wlroots
        ["wayshot", "--file", path],
        # gnome-screenshot — GNOME Wayland / Ubuntu
        ["gnome-screenshot", f"--file={path}"],
        # spectacle — KDE Plasma (-b background, -f fullscreen, -n sem notificação)
        ["spectacle", "-b", "-f", "-n", "-o", path],
        # flameshot — multi-DE
        ["flameshot", "full", "--path", path],
        # mate-screenshot — MATE
        ["mate-screenshot", "--file", path],
        # xfce4-screenshooter — XFCE
        ["xfce4-screenshooter", "--fullscreen", "--save", path],
        # maim / scrot — XWayland fallback
        ["maim", path],
        ["scrot", path],
    ]
    for cmd in candidates:
        tool_bin = _which(cmd[0])
        if not tool_bin:
            continue
        cmd[0] = tool_bin
        if _try_tool(cmd, path):
            return True
    return False


def _grab_wayland_region(x: int, y: int, w: int, h: int, path: str) -> bool:
    """Tenta capturar uma região no Wayland. Salva em `path`. Retorna True se ok."""
    candidates: list[list[str]] = [
        ["grim", "-g", f"{x},{y} {w}x{h}", path],
        ["wayshot", "--slurp", f"{x},{y} {w}x{h}", "--file", path],
        ["flameshot", "full", "--path", path],
        ["maim", "-g", f"{w}x{h}+{x}+{y}", path],
        ["scrot", "-a", f"{x},{y},{w},{h}", path],
    ]
    for cmd in candidates:
        tool_bin = _which(cmd[0])
        if not tool_bin:
            continue
        cmd[0] = tool_bin
        if _try_tool(cmd, path):
            return True
    return False


# ── Recorte de monitor específico ────────────────────────────────────────────

def _crop_to_screen(pixmap: QPixmap, screen) -> QPixmap:
    """Recorta o pixmap para a geometria física de um monitor específico."""
    if screen is None:
        return pixmap
    geo: QRect = screen.geometry()
    virtual_origin = QApplication.primaryScreen().virtualGeometry().topLeft()
    rel_x = geo.x() - virtual_origin.x()
    rel_y = geo.y() - virtual_origin.y()
    cropped = pixmap.copy(rel_x, rel_y, geo.width(), geo.height())
    return cropped if not cropped.isNull() else pixmap


# ── Captura de região ─────────────────────────────────────────────────────────

def grab_region(x: int, y: int, w: int, h: int) -> QPixmap | None:
    """Captura uma região. Wayland tenta várias ferramentas; X11 usa grabWindow."""
    if not _IS_WAYLAND:
        screen = QApplication.primaryScreen()
        px = screen.grabWindow(0, x, y, w, h)
        return px if not px.isNull() else None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        if not _grab_wayland_region(x, y, w, h, tmp):
            return None
        px = QPixmap(tmp)
        return px if not px.isNull() else None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── API pública de captura ────────────────────────────────────────────────────

def capture(toolbar_window, tray_icon=None,
            copy_to_clipboard: bool = False,
            screen=None) -> None:
    """Captura a tela ocultando a toolbar.
    `screen` (QScreen opcional): limita a captura ao monitor especificado.
    """
    toolbar_window.hide()

    def _do_capture():
        _SAVE_DIR.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = _SAVE_DIR / f"epicpen_{ts}.png"

        pixmap = _capture_to_file(dest, screen)
        toolbar_window.show()

        if pixmap is None:
            tools = _available_tools()
            hint = (
                f"Ferramentas encontradas: {', '.join(tools)}\nNenhuma retornou imagem válida."
                if tools else
                "Nenhuma ferramenta de captura encontrada.\n"
                "Instale uma das opções compatíveis:\n"
                "  Wayland: grim, wayshot, gnome-screenshot, spectacle, flameshot\n"
                "  X11: maim, scrot, flameshot"
            )
            _notify(tray_icon, "EpicPen — Screenshot falhou", hint)
            return

        if copy_to_clipboard:
            QApplication.clipboard().setPixmap(pixmap, QClipboard.Mode.Clipboard)

        msg = f"Salva em:\n~/Imagens/EpicPen/{dest.name}"
        if copy_to_clipboard:
            msg += "\n(copiada para área de transferência)"
        _notify(tray_icon, "EpicPen — Screenshot", msg)

    QTimer.singleShot(80, _do_capture)


def _capture_to_file(dest: Path, screen=None) -> QPixmap | None:
    """Captura a tela e salva em `dest`. Retorna o QPixmap ou None se falhar."""
    if not _IS_WAYLAND:
        src = screen or QApplication.primaryScreen()
        px = src.grabWindow(0)
        if px.isNull():
            return None
        if screen is not None:
            px = _crop_to_screen(px, screen)
        if not px.save(str(dest)):
            return None
        return px

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        if not _grab_wayland_fullscreen(tmp):
            return None
        px = QPixmap(tmp)
        if px.isNull():
            return None
        if screen is not None:
            px = _crop_to_screen(px, screen)
            # QPixmap.save() pode falhar silenciosamente no Wayland;
            # salvar em tmp2 e copiar binário garante escrita real.
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f2:
                tmp2 = f2.name
            try:
                if not px.save(tmp2):
                    return None
                shutil.copy2(tmp2, dest)
            finally:
                try:
                    os.unlink(tmp2)
                except OSError:
                    pass
        else:
            # Cópia binária direta: evita recompressão e QPixmap.save() silencioso
            shutil.copy2(tmp, dest)
        return px
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _notify(tray_icon, title: str, msg: str):
    if tray_icon:
        tray_icon.showMessage(title, msg, tray_icon.icon(), 4000)
