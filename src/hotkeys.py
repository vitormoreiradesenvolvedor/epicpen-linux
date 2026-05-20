"""
Listener global de teclado — captura teclas mesmo quando o app não tem foco.

Backends em ordem de preferência:
  1. KGlobalAccel (KDE DBus) — nativo Wayland, sem grupo input, sem pynput
  2. evdev (lê /dev/input/ diretamente) — puro Wayland, requer grupo 'input'
  3. pynput/X11 — fallback para sessões XWayland / X11 puro

Para ativar evdev: sudo usermod -aG input $USER  (logout para aplicar)
"""
import threading
from PyQt6.QtCore import QObject, pyqtSignal

# Evita chamar DBusGMainLoop(set_as_default=True) mais de uma vez
_GLIB_MAINLOOP_INIT = False


class GlobalHotkeyListener(QObject):
    """Emite toggled() quando o atalho global de toggle de desenho é pressionado."""

    toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._thread = None
        self._pynput_listener = None
        self._kga_loop = None

    def start(self) -> bool:
        """Tenta iniciar em ordem de preferência. Retorna True se algum backend ativou."""
        # evdev lê /dev/input diretamente — ignora o keyboard-shortcuts-inhibitor
        # do Wayland (ativo em terminais fullscreen). É o único backend confiável
        # nesse cenário, mas requer o grupo 'input'.
        if self._try_evdev():
            print("[hotkeys] ativo via evdev (nativo, todos os cenários)")
            return True
        # KGlobalAccel funciona na maioria dos casos Wayland, mas é bloqueado por
        # apps que usam zwp_keyboard_shortcuts_inhibitor (terminais fullscreen etc).
        if self._try_kglobalaccel():
            print("[hotkeys] ativo via KGlobalAccel "
                  "(não funciona com terminal fullscreen — veja abaixo)")
            print("[hotkeys] Para Tab funcionar em qualquer janela:")
            print("[hotkeys]   sudo usermod -aG input $USER   # depois logout/login")
            return True
        if self._try_pynput():
            print("[hotkeys] ativo via pynput/X11")
            return True
        print(
            "[hotkeys] atalho global indisponível.\n"
            "  Fix: sudo usermod -aG input $USER  (depois logout/login)"
        )
        return False

    # ── KGlobalAccel ──────────────────────────────────────────────────────────

    _ACTION = ["epicpen", "main", "toggle-drawing",
               "EpicPen: Ativar/desativar desenho"]
    _TAB_KEY = 16777217  # Qt::Key_Tab

    def _try_kglobalaccel(self) -> bool:
        """KDE KGlobalAccel via DBus — funciona em Wayland sem grupo input."""
        global _GLIB_MAINLOOP_INIT
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib

            if not _GLIB_MAINLOOP_INIT:
                DBusGMainLoop(set_as_default=True)
                _GLIB_MAINLOOP_INIT = True

            bus = dbus.SessionBus()
            kga = dbus.Interface(
                bus.get_object("org.kde.kglobalaccel", "/kglobalaccel"),
                "org.kde.KGlobalAccel",
            )

            action = dbus.Array(self._ACTION, signature="s")
            kga.doRegister(action)
            # Define Tab como atalho padrão (preserva mudanças do usuário em
            # sessões anteriores porque KGlobalAccel persiste as configurações)
            kga.setForeignShortcut(action, dbus.Array([self._TAB_KEY], signature="i"))

            def _on_pressed(component, shortcut, timestamp):
                if str(component) == "epicpen":
                    self.toggled.emit()

            bus.add_signal_receiver(
                _on_pressed,
                signal_name="globalShortcutPressed",
                dbus_interface="org.kde.kglobalaccel.Component",
                path="/component/epicpen",
            )

            # Loop GLib em thread daemon para despachar sinais DBus
            loop = GLib.MainLoop()
            self._kga_loop = loop
            threading.Thread(target=loop.run, daemon=True).start()
            return True

        except Exception as e:
            return False

    # ── evdev ─────────────────────────────────────────────────────────────────

    def _try_evdev(self) -> bool:
        try:
            import evdev
            from evdev import InputDevice, ecodes, list_devices

            keyboards = []
            for path in list_devices():
                try:
                    dev = InputDevice(path)
                    if ecodes.EV_KEY in dev.capabilities():
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

    # ── pynput/X11 ────────────────────────────────────────────────────────────

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
        if self._kga_loop is not None:
            try:
                self._kga_loop.quit()
            except Exception:
                pass
        if self._pynput_listener:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
