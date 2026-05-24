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
        # gnome-screenshot — GNOME Wayland
        lambda p: (["gnome-screenshot", f"--file={p}"], {}),
        # spectacle — KDE Plasma
        lambda p: (["spectacle", "--background", "--fullscreen", f"--output={p}"], {}),
        # scrot — X11 / XWayland fallback
        lambda p: (["scrot", p], {}),
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
    """Captura uma região com grim (wlroots) ou retorna False."""
    if not shutil.which("grim"):
        return False
    try:
        cmd = ["grim", "-g", f"{x},{y} {w}x{h}", path]
        r = subprocess.run(cmd, capture_output=True, timeout=2)
        return r.returncode == 0 and Path(path).exists()
    except Exception:
        return False


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
    """Captura uma região. Wayland usa grim -g; X11 usa grabWindow."""
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
            _notify(tray_icon,
                    "EpicPen — Screenshot falhou",
                    "Nenhuma ferramenta de captura disponível.\n"
                    "Instale 'grim' (wlroots) ou 'gnome-screenshot'.")
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
