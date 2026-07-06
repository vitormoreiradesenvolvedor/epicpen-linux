"""Rastreio global do cursor no KDE Wayland via KWin Scripting.

No Wayland o Qt só vê o ponteiro sobre janelas do próprio app —
QCursor.pos() congela fora delas. Um mini-script carregado no KWin lê
workspace.cursorPos num timer e envia por DBus para cá (técnica do
kdotool); a lupa segue a seta em qualquer ponto de qualquer monitor.

Nome de serviço/plugin fixos: o instance_guard garante instância única e
um script órfão de execução anterior é substituído no próximo start().
"""
import os
import tempfile

from PyQt6.QtCore import QObject, QPoint, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusInterface

_SERVICE = "org.epicpen.CursorFeed"
_PLUGIN = "epicpen-cursor-feed"

_SCRIPT = """\
let lx = -1, ly = -1, beat = 0;
const timer = new QTimer();
timer.interval = 33;
timer.timeout.connect(() => {
    const p = workspace.cursorPos;
    beat += 1;
    // só envia quando muda; heartbeat ~1s mantém o feed observável
    if (p.x !== lx || p.y !== ly || beat >= 30) {
        lx = p.x; ly = p.y; beat = 0;
        callDBus("%(svc)s", "/cursor", "%(svc)s", "update", p.x, p.y);
    }
});
timer.start();
""" % {"svc": _SERVICE}


class KWinCursorTracker(QObject):
    """Recebe update(x, y) do script no KWin e guarda a última posição."""

    def __init__(self):
        super().__init__()
        self._pos: QPoint | None = None
        self._registered = False
        self._script_path: str | None = None
        self._running = False

    @pyqtSlot(int, int)
    def update(self, x: int, y: int):
        self._pos = QPoint(x, y)

    def pos(self) -> QPoint | None:
        return self._pos

    def start(self) -> bool:
        """Carrega e roda o script no KWin. False fora do KDE (sem efeito)."""
        if self._running:
            return True
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        if not self._registered:
            ok_srv = bus.registerService(_SERVICE)
            ok_obj = bus.registerObject(
                "/cursor", _SERVICE, self,
                QDBusConnection.RegisterOption.ExportAllSlots,
            )
            self._registered = ok_srv and ok_obj
            if not self._registered:
                return False

        scripting = QDBusInterface("org.kde.KWin", "/Scripting",
                                   "org.kde.kwin.Scripting", bus)
        if not scripting.isValid():
            return False
        # Remove script órfão de uma execução anterior que tenha crashado
        scripting.call("unloadScript", _PLUGIN)

        if self._script_path is None or not os.path.exists(self._script_path):
            fd, path = tempfile.mkstemp(prefix="epicpen-cursor-", suffix=".js")
            with os.fdopen(fd, "w") as f:
                f.write(_SCRIPT)
            self._script_path = path

        r = scripting.call("loadScript", self._script_path, _PLUGIN)
        args = r.arguments()
        if not args or int(args[0]) < 0:
            return False
        script = QDBusInterface(
            "org.kde.KWin", f"/Scripting/Script{int(args[0])}",
            "org.kde.kwin.Script", bus,
        )
        script.call("run")
        self._running = True
        return True

    def stop(self):
        """Descarrega o script — nenhum timer fica rodando no KWin."""
        if not self._running:
            return
        self._running = False
        self._pos = None
        bus = QDBusConnection.sessionBus()
        QDBusInterface("org.kde.KWin", "/Scripting",
                       "org.kde.kwin.Scripting", bus).call(
            "unloadScript", _PLUGIN)
        if self._script_path is not None:
            try:
                os.unlink(self._script_path)
            except OSError:
                pass
            self._script_path = None
