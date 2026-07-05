import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QRect, QEventLoop
from PyQt6.QtGui import QClipboard, QPixmap

_SAVE_DIR = Path.home() / "Imagens" / "EpicPen"

_IS_WAYLAND = (
    os.environ.get("WAYLAND_DISPLAY") is not None
    and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"
)


# ── Ferramentas disponíveis ───────────────────────────────────────────────────

# Ferramentas que capturam REGIÃO por coordenadas (usadas pela lupa).
# Mesma ordem de _grab_wayland_region.
_REGION_TOOLS = ("grim", "wayshot", "flameshot", "maim", "scrot")

# Sonda funcional das ferramentas de região (None = ainda não sondado)
_region_tools_ok: bool | None = None


def _tool_path(name: str) -> str | None:
    """Resolve a ferramenta: sistema primeiro; senão a bundlada no AppImage.

    O usr/bin do AppDir fica fora do PATH de propósito — só o grim é
    distribuído lá (além do ffmpeg, que o recorder resolve por conta)."""
    p = shutil.which(name)
    if p:
        return p
    appdir = os.environ.get("APPDIR")
    if appdir:
        cand = os.path.join(appdir, "usr", "bin", name)
        if os.access(cand, os.X_OK):
            return cand
    return None


def has_region_tool() -> bool:
    """True se existe alguma ferramenta de captura de região (inclui bundlada)."""
    return any(_tool_path(t) for t in _REGION_TOOLS)


def _region_tools_work() -> bool:
    """Sonda (com cache por sessão) se alguma ferramenta captura região DE FATO.

    Ter o binário não basta: o grim (inclusive o bundlado) falha em
    compositores sem wlr-screencopy/ext-image-copy-capture (GNOME, KDE)."""
    global _region_tools_ok
    if _region_tools_ok is None:
        if not has_region_tool():
            _region_tools_ok = False
        else:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp = f.name
            try:
                _region_tools_ok = _grab_wayland_region(0, 0, 8, 8, tmp)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return _region_tools_ok


def region_backend() -> str | None:
    """Backend de região disponível no Wayland, por prioridade:
    'kwin' (DBus silencioso, ~8ms), 'tool' (grim & cia.) ou None."""
    import kwinshot
    if kwinshot.authorized():
        return "kwin"
    if _region_tools_work():
        return "tool"
    return None


def _available_tools() -> list[str]:
    known = [
        "grim", "wayshot", "hyprshot",
        "gnome-screenshot", "spectacle", "flameshot",
        "mate-screenshot", "xfce4-screenshooter",
        "maim", "scrot",
    ]
    return [t for t in known if _tool_path(t)]


# ── Execução de ferramentas ───────────────────────────────────────────────────

def _try_tool(cmd: list[str], path: str, timeout: int = 10) -> bool:
    """Executa a ferramenta e verifica se produziu um arquivo válido.

    env=host_env(): dentro do AppImage, o LD_LIBRARY_PATH bundlado quebra
    ferramentas Qt do sistema (spectacle) — sem o ambiente limpo, todas
    falhavam e o screenshot caía sempre no fallback de portal.
    """
    from hostenv import host_env
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           env=host_env())
        return r.returncode == 0 and Path(path).exists() and Path(path).stat().st_size > 0
    except Exception:
        return False


def _grab_wayland_fullscreen(path: str) -> bool:
    """Tenta capturar toda a tela no Wayland. Salva em `path`. Retorna True se ok."""
    # KWin ScreenShot2: silencioso e sem subprocesso — antes das ferramentas
    import kwinshot
    if kwinshot.authorized():
        px = kwinshot.grab_virtual_desktop()
        if px is not None and px.save(path):
            return True
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
        exe = _tool_path(cmd[0])
        if exe is None:
            continue
        if _try_tool([exe] + cmd[1:], path):
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
        exe = _tool_path(cmd[0])
        if exe is None:
            continue
        if _try_tool([exe] + cmd[1:], path):
            return True
    return False


# ── Captura nativa Qt (bundled no AppImage) ───────────────────────────────────

def _grab_via_qt(path: str, screen=None, timeout_ms: int = 4000) -> bool:
    """Captura um frame único via QScreenCapture (Qt Multimedia).

    Não depende de nenhuma ferramenta do sistema — Qt Multimedia vem bundled
    no AppImage e usa o portal de ScreenCast no Wayland (mesmo mecanismo do
    gravador). Captura apenas o monitor indicado (ou o primário).

    O primeiro frame de alguns compositores chega preto/incompleto; espera-se
    o segundo frame válido, usando o primeiro como reserva no timeout.
    """
    try:
        from PyQt6.QtMultimedia import (
            QMediaCaptureSession, QScreenCapture, QVideoSink,
        )
    except ImportError:
        return False

    import time as _time

    capture = QScreenCapture()
    sink = QVideoSink()
    session = QMediaCaptureSession()
    session.setScreenCapture(capture)
    session.setVideoSink(sink)
    if screen is not None:
        capture.setScreen(screen)

    loop = QEventLoop()
    state: dict = {"img": None, "count": 0}
    t0 = _time.monotonic()

    def _on_frame(frame):
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        state["img"] = img.copy()
        state["count"] += 1
        # Warm-up: o KDE entrega frames pretos no início da sessão de
        # ScreenCast — espera ≥2 frames E ≥250ms antes de aceitar
        if state["count"] >= 2 and _time.monotonic() - t0 >= 0.25:
            loop.quit()

    sink.videoFrameChanged.connect(_on_frame)
    capture.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()

    # Encerra a sessão de ScreenCast de verdade: sem o deleteLater, cada
    # captura deixava um ícone de "transmissão de tela" pendurado no KDE
    try:
        sink.videoFrameChanged.disconnect(_on_frame)
    except TypeError:
        pass
    capture.stop()
    session.setScreenCapture(None)
    session.setVideoSink(None)
    capture.deleteLater()
    sink.deleteLater()
    session.deleteLater()

    img = state["img"]
    if img is None:
        return False
    return bool(img.save(path))


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

    import kwinshot
    if kwinshot.authorized():
        px = kwinshot.grab_region(x, y, w, h)
        if px is not None:
            return px

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

def failure_hint() -> str:
    """Mensagem de erro com as ferramentas encontradas (ou ausentes)."""
    tools = _available_tools()
    if tools:
        return (
            f"Ferramentas encontradas: {', '.join(tools)}\n"
            "Nenhuma retornou imagem válida."
        )
    return (
        "Nenhuma ferramenta de captura encontrada e a captura via portal falhou.\n"
        "Instale uma das opções compatíveis:\n"
        "  Wayland: grim, wayshot, gnome-screenshot, spectacle, flameshot\n"
        "  X11: maim, scrot, flameshot"
    )


def save_pixmap(pixmap: QPixmap) -> Path | None:
    """Salva o pixmap em _SAVE_DIR com nome timestampado. None se falhar."""
    _SAVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = _SAVE_DIR / f"epicpen_{ts}.png"
    return dest if pixmap.save(str(dest)) else None


def _grab_pixmap(screen=None) -> QPixmap | None:
    """Captura a tela e retorna o QPixmap cru, sem salvar em disco."""
    if not _IS_WAYLAND:
        src = screen or QApplication.primaryScreen()
        px = src.grabWindow(0)
        if px.isNull():
            return None
        if screen is not None:
            px = _crop_to_screen(px, screen)
        return px

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        if _grab_wayland_fullscreen(tmp):
            px = QPixmap(tmp)
            if px.isNull():
                return None
            return _crop_to_screen(px, screen) if screen is not None else px
        # Fallback garantido (bundled): QScreenCapture já captura por-monitor
        if _grab_via_qt(tmp, screen):
            px = QPixmap(tmp)
            return px if not px.isNull() else None
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def capture_for_edit(toolbar_window, screen=None, on_result=None) -> None:
    """Captura e entrega o QPixmap cru ao chamador — sem salvar.

    O chamador decide recortar/salvar/copiar (CropDialog na toolbar).
    on_result(pixmap | None, hint | None) é chamado após restaurar a toolbar.
    """
    toolbar_window.hide()

    def _do_capture():
        px = _grab_pixmap(screen)
        toolbar_window.show()
        if on_result is None:
            return
        if px is None:
            on_result(None, failure_hint())
        else:
            on_result(px, None)

    QTimer.singleShot(80, _do_capture)

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
            _notify(tray_icon, "EpicPen — Screenshot falhou", failure_hint())
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
        if _grab_wayland_fullscreen(tmp):
            px = QPixmap(tmp)
            if px.isNull():
                return None
            if screen is not None:
                px = _crop_to_screen(px, screen)
                if not px.save(str(dest)):
                    return None
            else:
                # Cópia binária direta: evita recompressão e QPixmap.save() silencioso
                shutil.copy2(tmp, dest)
            return px

        # Fallback garantido: QScreenCapture (Qt Multimedia, bundled no AppImage).
        # Captura já é por-monitor — sem crop posterior.
        if _grab_via_qt(tmp, screen):
            px = QPixmap(tmp)
            if px.isNull():
                return None
            shutil.copy2(tmp, dest)
            return px
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _notify(tray_icon, title: str, msg: str):
    if tray_icon:
        tray_icon.showMessage(title, msg, tray_icon.icon(), 4000)
