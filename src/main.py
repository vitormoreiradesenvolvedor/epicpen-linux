#!/usr/bin/env python3
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

import config as cfg
from overlay import OverlayWindow
from toolbar import ToolbarWindow
from tray import TrayIcon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("EpicPen")
    app.setApplicationVersion("0.1.0")
    app.setQuitOnLastWindowClosed(False)

    settings = cfg.load()

    overlay = OverlayWindow()
    toolbar = ToolbarWindow(overlay, config=settings)
    tray    = TrayIcon(overlay, toolbar, app)
    toolbar.set_tray(tray)

    # Restaura modo quadro branco salvo
    if settings.get("whiteboard"):
        overlay.set_whiteboard(True)
        toolbar._btn_whiteboard.setChecked(True)

    overlay.show()
    toolbar.show()

    # Autosave com debounce de 500 ms para não escrever em disco a cada evento
    _save_timer = QTimer()
    _save_timer.setSingleShot(True)
    _save_timer.setInterval(500)
    _save_timer.timeout.connect(lambda: cfg.save(toolbar.get_state()))

    def schedule_save():
        _save_timer.start()

    # Conecta mudanças que precisam ser persistidas
    toolbar._size_slider.valueChanged.connect(lambda _: schedule_save())
    toolbar._zoom_slider.valueChanged.connect(lambda _: schedule_save())
    toolbar._btn_whiteboard.toggled.connect(lambda _: schedule_save())
    toolbar._color_btn.clicked.connect(lambda: schedule_save())
    for b in toolbar._tool_buttons:
        b.clicked.connect(lambda _: schedule_save())

    # Salva posição da toolbar ao mover
    _move_timer = QTimer()
    _move_timer.setSingleShot(True)
    _move_timer.setInterval(800)
    _move_timer.timeout.connect(lambda: cfg.save(toolbar.get_state()))

    original_release = toolbar.mouseReleaseEvent

    def _on_release(event):
        original_release(event)
        _move_timer.start()

    toolbar.mouseReleaseEvent = _on_release

    # Salva ao fechar
    app.aboutToQuit.connect(lambda: cfg.save(toolbar.get_state()))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
