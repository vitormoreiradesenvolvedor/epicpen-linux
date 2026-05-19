"""
Listener global de teclado — captura teclas mesmo quando o app não tem foco.

Backend preferido: evdev (lê /dev/input/ diretamente — puro Wayland).
  Requer grupo 'input': sudo usermod -aG input $USER  (logout para aplicar)

Fallback: pynput X11/XWayland (funciona quando DISPLAY está disponível).
"""
import threading
from PyQt6.QtCore import QObject, pyqtSignal


class GlobalHotkeyListener(QObject):
    """Emite toggled() quando o atalho de toggle de desenho é pressionado."""

    toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._thread = None
        self._pynput_listener = None

    def start(self) -> bool:
        """Tenta iniciar. Retorna True se algum backend funcionou."""
        if self._try_evdev():
            print("[hotkeys] ativo via evdev (Wayland nativo)")
            return True
        if self._try_pynput():
            print("[hotkeys] ativo via pynput/X11")
            return True
        print(
            "[hotkeys] atalho global indisponível.\n"
            "  Para ativar: sudo usermod -aG input $USER  (depois logout/login)"
        )
        return False

    # ── backends ──────────────────────────────────────────────────────────────

    def _try_evdev(self) -> bool:
        try:
            import evdev
            from evdev import InputDevice, ecodes, list_devices

            keyboards = []
            for path in list_devices():
                try:
                    dev = InputDevice(path)
                    caps = dev.capabilities()
                    if ecodes.EV_KEY in caps:
                        keyboards.append(dev)
                except Exception:
                    continue

            if not keyboards:
                return False

            stop = self._stop_event

            def _listen():
                import select
                while not stop.is_set():
                    fds = {kb.fd: kb for kb in keyboards}
                    r, _, _ = select.select(list(fds.keys()), [], [], 0.2)
                    for fd in r:
                        try:
                            for ev in fds[fd].read():
                                if (ev.type == ecodes.EV_KEY
                                        and ev.code == ecodes.KEY_TAB
                                        and ev.value == 1):
                                    self.toggled.emit()
                        except Exception:
                            pass

            self._thread = threading.Thread(target=_listen, daemon=True)
            self._thread.start()
            return True
        except Exception:
            return False

    def _try_pynput(self) -> bool:
        try:
            from pynput import keyboard as kb

            def on_press(key):
                if key == kb.Key.tab:
                    self.toggled.emit()

            listener = kb.Listener(on_press=on_press)
            listener.daemon = True
            listener.start()
            self._pynput_listener = listener
            return True
        except Exception:
            return False

    # ── cleanup ───────────────────────────────────────────────────────────────

    def stop(self):
        self._stop_event.set()
        if self._pynput_listener:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
