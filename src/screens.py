"""Validação defensiva de QScreen contra ponteiros pendentes.

No Wayland/KWin uma reconfiguração de saída (mudança de modo, DPMS, hotplug e,
em setups mixed-DPI/multi-GPU, até certos drags) emite screenRemoved e DELETA o
objeto C++ do QScreen. Qualquer referência Python guardada (toolbar._current_screen,
magnifier._ls_screen, o QScreen do QScreenCapture…) vira ponteiro pendente —
acessar screen.geometry()/windowHandle().setScreen(screen) causa SIGSEGV sem
traceback. Estas funções verificam o QScreen contra a lista viva antes do uso.
"""
def screen_alive(scr) -> bool:
    """True se `scr` ainda é um QScreen válido e conectado.

    `scr in QApplication.screens()` cobre tanto o None quanto o wrapper C++ já
    deletado (que levanta RuntimeError ao ser comparado)."""
    if scr is None:
        return False
    from PyQt6.QtWidgets import QApplication
    try:
        return scr in QApplication.screens()
    except RuntimeError:
        return False


def safe_screen(scr):
    """Retorna `scr` se vivo; senão o primaryScreen; senão None.

    Nunca levanta — usar sempre que um QScreen guardado for reutilizado após
    uma possível troca de monitor."""
    if screen_alive(scr):
        return scr
    from PyQt6.QtWidgets import QApplication
    try:
        primary = QApplication.primaryScreen()
    except RuntimeError:
        return None
    return primary if screen_alive(primary) else None
