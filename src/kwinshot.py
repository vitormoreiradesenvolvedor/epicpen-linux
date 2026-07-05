"""Captura silenciosa via org.kde.KWin.ScreenShot2 (KDE Wayland).

O mesmo caminho do Spectacle/Flameshot: uma chamada DBus por captura
(~8ms), sem diálogo de portal e sem indicador de transmissão — ideal
para a lupa seguir o cursor.

Autorização: o KWin resolve /proc/<pid>/exe do chamador e procura um
.desktop instalado cujo primeiro token do Exec canonicalize para o mesmo
executável e declare X-KDE-DBUS-Restricted-Interfaces com esta interface
(kwin/src/utils/serviceutils.h). Como o AppImage monta o intérprete num
caminho novo a cada execução, ensure_authorization() reescreve o .desktop
auxiliar a cada arranque e dispara o kbuildsycoca6.
"""
import os
import select
import subprocess
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QMetaType, QVariant
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtDBus import (
    QDBusConnection, QDBusMessage, QDBusUnixFileDescriptor,
)

_SERVICE = "org.kde.KWin.ScreenShot2"
_PATH = "/org/kde/KWin/ScreenShot2"
_IFACE = "org.kde.KWin.ScreenShot2"

# Arquivos separados para AppImage e dev: alternar entre os dois modos não
# fica reescrevendo (e re-indexando) o mesmo .desktop a cada arranque
_APPS_DIR = Path.home() / ".local" / "share" / "applications"


def _desktop_file() -> Path:
    name = ("epicpen-kwin-capture.desktop" if os.environ.get("APPDIR")
            else "epicpen-kwin-capture-dev.desktop")
    return _APPS_DIR / name

# None = nunca sondado; False é re-sondado (o kbuildsycoca6 pode terminar
# depois do arranque); True vale para a sessão toda. Falta do serviço
# (fora do KDE) cacheia False definitivo via _service_missing.
_authorized: bool | None = None
_service_missing = False


def _uint(n: int) -> QVariant:
    v = QVariant(int(n))
    v.convert(QMetaType(QMetaType.Type.UInt.value))
    return v


def _capture(method: str, args: list) -> QImage | None:
    """Chama um método Capture* do KWin e lê a imagem crua do pipe.

    A leitura corre numa thread: o KWin só responde depois de escrever a
    imagem, e regiões grandes não cabem no buffer do pipe — ler apenas
    após a resposta deadlockaria.
    """
    global _service_missing
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None

    r, w = os.pipe()
    data = bytearray()
    # O leitor não pode confiar no EOF: a QDBusMessage guarda um dup do fd
    # de escrita enquanto viver. Ele para ao atingir `expected` (definido
    # pela resposta do KWin: stride*height; 0 = erro, nada virá).
    state = {"expected": None}

    def _reader():
        try:
            while True:
                exp = state["expected"]
                if exp is not None and len(data) >= exp:
                    break
                ready, _, _ = select.select([r], [], [], 0.25)
                if ready:
                    chunk = os.read(r, 1 << 16)
                    if not chunk:
                        break
                    data.extend(chunk)
                elif exp == 0:
                    break
        except OSError:
            pass

    t = threading.Thread(target=_reader, daemon=True,
                         name="epicpen-kwinshot")
    t.start()

    msg = QDBusMessage.createMethodCall(_SERVICE, _PATH, _IFACE, method)
    msg.setArguments(args + [{"native-resolution": True},
                             QDBusUnixFileDescriptor(w)])
    reply = bus.call(msg, timeout=5000)
    del msg
    os.close(w)

    res = None
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        if reply.errorName() == "org.freedesktop.DBus.Error.ServiceUnknown":
            _service_missing = True
        state["expected"] = 0
    else:
        try:
            res = reply.arguments()[0]
            state["expected"] = int(res["stride"]) * int(res["height"])
        except (IndexError, KeyError, TypeError, ValueError):
            state["expected"] = 0
            res = None

    t.join(timeout=5)
    os.close(r)

    expected = state["expected"]
    if res is None or not expected or len(data) < expected:
        return None
    img = QImage(bytes(data[:expected]), int(res["width"]),
                 int(res["height"]), int(res["stride"]),
                 QImage.Format(int(res["format"])))
    return img if not img.isNull() else None


# ── API pública ───────────────────────────────────────────────────────────────

def grab_region(x: int, y: int, w: int, h: int) -> QPixmap | None:
    img = _capture("CaptureArea", [int(x), int(y), _uint(w), _uint(h)])
    return QPixmap.fromImage(img) if img is not None else None


def grab_virtual_desktop() -> QPixmap | None:
    """Captura toda a área virtual (equivale ao fullscreen do grim)."""
    from PyQt6.QtWidgets import QApplication
    primary = QApplication.primaryScreen()
    if primary is None:
        return None
    geo = primary.virtualGeometry()
    return grab_region(geo.x(), geo.y(), geo.width(), geo.height())


def authorized() -> bool:
    """Sonda (com cache) se o KWin nos deixa capturar sem prompt."""
    global _authorized
    if _authorized:
        return True
    if _service_missing:
        return False
    _authorized = _capture("CaptureArea", [0, 0, _uint(1), _uint(1)]) is not None
    return _authorized


def _interpreter_is_private() -> bool:
    """True se o intérprete é exclusivo do EpicPen: o bundlado no AppImage
    ou um python DENTRO do projeto (venv criado com cópia — run.sh).
    Autorizar um intérprete do sistema liberaria captura silenciosa para
    qualquer script Python, então fica de fora."""
    if os.environ.get("APPDIR"):
        return True
    exe = os.path.realpath(sys.executable)
    root = os.path.realpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
    return exe.startswith(root + os.sep)


def ensure_authorization():
    """Instala/atualiza o .desktop que autoriza o intérprete privado
    (AppImage: o caminho muda a cada montagem; dev: python copiado no
    .venv do projeto)."""
    if not _interpreter_is_private():
        return
    exe = os.path.realpath(sys.executable)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=EpicPen (captura de tela KWin)\n"
        f'Exec="{exe}"\n'
        "NoDisplay=true\n"
        f"X-KDE-DBUS-Restricted-Interfaces={_IFACE}\n"
    )
    dest = _desktop_file()
    try:
        if dest.exists() and dest.read_text() == content:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    except OSError:
        return

    def _rebuild():
        from hostenv import host_env
        try:
            subprocess.run(
                ["kbuildsycoca6"], timeout=30, env=host_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # o kded reconstrói sozinho ao notar o arquivo novo

    threading.Thread(target=_rebuild, daemon=True,
                     name="epicpen-sycoca").start()
