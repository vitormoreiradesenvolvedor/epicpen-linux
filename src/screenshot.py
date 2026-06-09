import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QClipboard, QPixmap

_SAVE_DIR = Path.home() / "Imagens" / "EpicPen"

_IS_WAYLAND = (
    os.environ.get("WAYLAND_DISPLAY") is not None
    and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"
)


# ── Captura de tela ───────────────────────────────────────────────────────────

def _grab_fullscreen_x11() -> QPixmap | None:
    screen = QApplication.primaryScreen()
    px = screen.grabWindow(0)
    return px if not px.isNull() else None


def _grab_fullscreen_wayland(path: str) -> bool:
    """
    Tenta capturar toda a tela no Wayland usando ferramentas do sistema.
    Retorna True se salvou o arquivo com sucesso.
    """
    candidates = [
        # grim — wlroots (Hyprland, Sway, etc.)
        lambda p: (["grim", p], {}),
        # wayshot — alternativa wlroots
        lambda p: (["wayshot", "--file", p], {}),
        # hyprshot — Hyprland
        lambda p: (["hyprshot", "--mode", "output", "--filename", p], {}),
        # gnome-screenshot — GNOME Wayland / Ubuntu
        lambda p: (["gnome-screenshot", f"--file={p}"], {}),
        # spectacle — KDE Plasma
        lambda p: (["spectacle", "--background", "--fullscreen", f"--output={p}"], {}),
        # flameshot — multi-DE (GNOME, KDE, MATE, XFCE, etc.)
        lambda p: (["flameshot", "full", "--path", p], {}),
        # mate-screenshot — MATE Desktop
        lambda p: (["mate-screenshot", "--file", p], {}),
        # xfce4-screenshooter — XFCE
        lambda p: (["xfce4-screenshooter", "--fullscreen", "--save", p], {}),
        # maim — X11 / XWayland
        lambda p: (["maim", p], {}),
        # scrot — X11 / XWayland fallback
        lambda p: (["scrot", p], {}),
        # import — ImageMagick (último recurso)
        lambda p: (["import", "-window", "root", p], {}),
    ]
    for make_cmd in candidates:
        cmd, kwargs = make_cmd(path)
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=8, **kwargs)
            if r.returncode == 0 and Path(path).exists() and Path(path).stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def _grab_region_wayland(x: int, y: int, w: int, h: int, path: str) -> bool:
    """Captura uma região no Wayland usando a primeira ferramenta disponível."""
    region = f"{x},{y} {w}x{h}"
    candidates = [
        # grim — wlroots
        lambda p: ["grim", "-g", region, p],
        # wayshot — alternativa wlroots
        lambda p: ["wayshot", "--slurp", region, "--file", p],
        # flameshot — multi-DE (ignora coordenadas, abre GUI de seleção)
        lambda p: ["flameshot", "full", "--path", p],
        # maim — X11 / XWayland
        lambda p: ["maim", "-g", f"{w}x{h}+{x}+{y}", p],
        # scrot — X11 / XWayland
        lambda p: ["scrot", "-a", f"{x},{y},{w},{h}", p],
    ]
    for make_cmd in candidates:
        cmd = make_cmd(path)
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=8)
            if r.returncode == 0 and Path(path).exists() and Path(path).stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def _available_tools() -> list[str]:
    """Retorna os nomes das ferramentas de captura instaladas no sistema."""
    known = [
        "grim", "wayshot", "hyprshot",
        "gnome-screenshot", "spectacle", "flameshot",
        "mate-screenshot", "xfce4-screenshooter",
        "maim", "scrot", "import",
    ]
    return [t for t in known if shutil.which(t)]


def grab_screen() -> QPixmap | None:
    """Captura toda a tela. Funciona em X11 e Wayland."""
    if not _IS_WAYLAND:
        return _grab_fullscreen_x11()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        if _grab_fullscreen_wayland(tmp):
            px = QPixmap(tmp)
            return px if not px.isNull() else None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None


def grab_region(x: int, y: int, w: int, h: int) -> QPixmap | None:
    """Captura uma região. Wayland tenta várias ferramentas; X11 usa grabWindow."""
    if not _IS_WAYLAND:
        screen = QApplication.primaryScreen()
        px = screen.grabWindow(0, x, y, w, h)
        return px if not px.isNull() else None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        if _grab_region_wayland(x, y, w, h, tmp):
            px = QPixmap(tmp)
            return px if not px.isNull() else None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None


# ── API pública ───────────────────────────────────────────────────────────────

def capture(toolbar_window, tray_icon=None, copy_to_clipboard: bool = False) -> None:
    """
    Captura toda a tela (incluindo anotações do overlay).
    Oculta a toolbar antes de capturar e a restaura depois.
    """
    toolbar_window.hide()

    def _do_capture():
        pixmap = grab_screen()
        toolbar_window.show()

        if pixmap is None or pixmap.isNull():
            tools = _available_tools()
            if tools:
                hint = f"Ferramentas encontradas: {', '.join(tools)}\nNenhuma retornou imagem válida."
            else:
                hint = (
                    "Nenhuma ferramenta de captura encontrada.\n"
                    "Instale uma das opções compatíveis:\n"
                    "  Wayland: grim, wayshot, gnome-screenshot, spectacle, flameshot\n"
                    "  X11: maim, scrot, flameshot"
                )
            _notify(tray_icon, "EpicPen — Screenshot falhou", hint)
            return

        _SAVE_DIR.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _SAVE_DIR / f"epicpen_{ts}.png"
        pixmap.save(str(path))

        if copy_to_clipboard:
            QApplication.clipboard().setPixmap(pixmap, QClipboard.Mode.Clipboard)

        msg = f"Salva em:\n~/Imagens/EpicPen/{path.name}"
        if copy_to_clipboard:
            msg += "\n(copiada para área de transferência)"
        _notify(tray_icon, "EpicPen — Screenshot", msg)

    QTimer.singleShot(80, _do_capture)


def _notify(tray_icon, title: str, msg: str):
    if tray_icon:
        tray_icon.showMessage(title, msg, tray_icon.icon(), 4000)
