"""
Define keepAbove=true para todas as janelas deste processo via KWin DBus scripting.
Funciona no KDE Plasma 6 Wayland sem roubar foco de teclado.
"""
import os
import tempfile
import threading


def _run(pid: int):
    try:
        import dbus
        js = (
            "var wins = workspace.windows();"
            f"for (var i = 0; i < wins.length; i++) {{"
            f"if (wins[i].pid === {pid}) wins[i].keepAbove = true;"
            f"}}"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(js)
            path = f.name
        try:
            bus = dbus.SessionBus()
            scripting = dbus.Interface(
                bus.get_object('org.kde.KWin', '/Scripting'),
                dbus_interface='org.kde.kwin.Scripting',
            )
            sid = int(scripting.loadScript(path))
            if sid >= 0:
                dbus.Interface(
                    bus.get_object('org.kde.KWin', f'/{sid}'),
                    dbus_interface='org.kde.kwin.Scripting',
                ).run()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as exc:
        print(f"[keepabove] {exc}", flush=True)


def set_keepabove():
    """Lança o script KWin em background; retorna imediatamente."""
    threading.Thread(target=_run, args=(os.getpid(),), daemon=True).start()
