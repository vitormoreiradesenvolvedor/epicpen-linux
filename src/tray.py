from pathlib import Path
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QFont
from PyQt6.QtCore import Qt, QRect

_ICON_FILE = Path(__file__).parent.parent / "resources" / "icons" / "epicpen.png"


def _make_icon() -> QIcon:
    """Carrega o ícone do arquivo PNG, ou gera um fallback programático."""
    if _ICON_FILE.exists():
        return QIcon(str(_ICON_FILE))

    # Fallback: círculo escuro + "EP" vermelho
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(30, 30, 30, 230)))
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QPen(QColor(200, 200, 200, 120), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(2, 2, 60, 60)
    font = QFont("Sans Serif", 20, QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(QPen(QColor(220, 60, 60)))
    p.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "EP")
    p.end()
    return QIcon(px)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, overlay, toolbar, app: QApplication):
        super().__init__(_make_icon(), app)
        self._overlay = overlay
        self._toolbar = toolbar
        self._visible = True

        self.setToolTip("EpicPen Linux")
        self._build_menu()

        self.activated.connect(self._on_activated)
        self.show()

    def _build_menu(self):
        menu = QMenu()

        self._act_toggle = menu.addAction("Ocultar janelas")
        self._act_toggle.triggered.connect(self._toggle_visibility)

        menu.addSeparator()

        act_whiteboard = menu.addAction("⬜  Quadro Branco")
        act_whiteboard.setCheckable(True)
        act_whiteboard.triggered.connect(self._toggle_whiteboard)
        self._act_whiteboard = act_whiteboard

        act_clear = menu.addAction("🗑  Limpar tela")
        act_clear.triggered.connect(self._overlay.clear)

        act_screenshot = menu.addAction("📷  Screenshot (Ctrl+S)")
        act_screenshot.triggered.connect(self._take_screenshot)

        act_screenshot_clip = menu.addAction("📋  Screenshot → Área de transferência")
        act_screenshot_clip.triggered.connect(self._take_screenshot_clipboard)

        menu.addSeparator()

        act_quit = menu.addAction("Sair")
        act_quit.triggered.connect(QApplication.instance().quit)

        self.setContextMenu(menu)

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visibility()

    def _toggle_visibility(self):
        self._visible = not self._visible
        if self._visible:
            self._overlay.show()
            self._toolbar.show()
            self._act_toggle.setText("Ocultar janelas")
        else:
            self._overlay.hide()
            self._toolbar.hide()
            self._act_toggle.setText("Mostrar janelas")

    def _toggle_whiteboard(self, checked: bool):
        self._overlay.set_whiteboard(checked)
        self._toolbar._btn_whiteboard.setChecked(checked)

    def _take_screenshot(self):
        import screenshot as sc
        sc.capture(self._toolbar, tray_icon=self, copy_to_clipboard=False)

    def _take_screenshot_clipboard(self):
        import screenshot as sc
        sc.capture(self._toolbar, tray_icon=self, copy_to_clipboard=True)
