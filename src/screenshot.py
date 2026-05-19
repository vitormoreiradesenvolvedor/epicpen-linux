from pathlib import Path
from datetime import datetime
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QClipboard

_SAVE_DIR = Path.home() / "Imagens" / "EpicPen"


def capture(toolbar_window, tray_icon=None, copy_to_clipboard: bool = False) -> None:
    """
    Captura toda a tela incluindo as anotações do overlay.
    Oculta a toolbar antes de capturar e a restaura depois.
    Exibe notificação via bandeja e opcionalmente copia para área de transferência.
    """
    toolbar_window.hide()

    def _do_capture():
        screen = QApplication.primaryScreen()
        # grabWindow(0) captura o desktop virtual inteiro (X11)
        pixmap = screen.grabWindow(0)

        _SAVE_DIR.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _SAVE_DIR / f"epicpen_{ts}.png"
        pixmap.save(str(path))

        toolbar_window.show()

        if copy_to_clipboard:
            QApplication.clipboard().setPixmap(
                pixmap, QClipboard.Mode.Clipboard
            )

        if tray_icon:
            msg = f"Salva em:\n~/Imagens/EpicPen/{path.name}"
            if copy_to_clipboard:
                msg += "\n(copiada para área de transferência)"
            tray_icon.showMessage("EpicPen — Screenshot", msg,
                                  tray_icon.icon(), 4000)

    # 80 ms para o sistema redesenhar sem a toolbar
    QTimer.singleShot(80, _do_capture)
