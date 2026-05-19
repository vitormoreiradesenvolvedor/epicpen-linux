#!/usr/bin/env python3
import sys
from PyQt6.QtWidgets import QApplication
from overlay import OverlayWindow
from toolbar import ToolbarWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("EpicPen")
    app.setApplicationVersion("0.1.0")
    app.setQuitOnLastWindowClosed(False)

    overlay = OverlayWindow()
    toolbar = ToolbarWindow(overlay)

    overlay.show()
    toolbar.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
