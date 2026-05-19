from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton,
    QSlider, QColorDialog, QLabel, QFrame
)
from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter, QBrush, QCursor

from magnifier import MagnifierWindow


class ToolbarWindow(QWidget):
    """Barra de ferramentas flutuante, arrastável, sempre no topo."""

    def __init__(self, overlay, config: dict | None = None):
        super().__init__()
        self._overlay = overlay
        self._drag_pos: QPoint | None = None
        self._drawing_active = True
        self._magnifier = MagnifierWindow()
        self._cfg = config or {}
        self._screenshot_fn = None       # injetado por main.py
        self._tray = None                # injetado por main.py

        color_hex = self._cfg.get("color", "#FF0000")
        self._current_color = QColor(color_hex)

        # ── Modo Apresentação ────────────────────────────────────────────
        self._presentation_mode = False
        # Oculta após 1.5s sem hover
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(1500)
        self._hide_timer.timeout.connect(self._presentation_auto_hide)
        # Detecta cursor perto da borda esquerda para reexibir
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(150)
        self._edge_timer.timeout.connect(self._check_edge_reveal)

        self._setup_window()
        self._build_ui()
        self._apply_config()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(56)
        pos = self._cfg.get("toolbar_pos", {"x": 20, "y": 150})
        self.move(pos.get("x", 20), pos.get("y", 150))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)

        self._container = QFrame(self)
        self._container.setObjectName("toolbar")
        self._container.setStyleSheet("""
            QFrame#toolbar {
                background-color: rgba(30, 30, 30, 220);
                border-radius: 12px;
                border: 1px solid rgba(255,255,255,30);
            }
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                color: white;
                font-size: 18px;
                padding: 4px;
                min-width: 36px;
                min-height: 36px;
            }
            QPushButton:hover  { background: rgba(255,255,255,30); }
            QPushButton:checked {
                background: rgba(255,255,255,60);
                border: 1px solid rgba(255,255,255,80);
            }
            QSlider::groove:vertical {
                background: rgba(255,255,255,40);
                width: 4px; border-radius: 2px;
            }
            QSlider::handle:vertical {
                background: white;
                width: 12px; height: 12px;
                margin: -4px -4px;
                border-radius: 6px;
            }
        """)

        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(6, 8, 6, 8)
        inner.setSpacing(4)

        def btn(icon: str, tooltip: str, checkable: bool = True) -> QPushButton:
            b = QPushButton(icon)
            b.setToolTip(tooltip)
            b.setCheckable(checkable)
            return b

        # ── Ferramentas de desenho ────────────────────────────────────────
        self._btn_pen    = btn("✏️", "Caneta (P)")
        self._btn_hl     = btn("🖊",  "Marcador (H)")
        self._btn_line   = btn("╱",   "Linha (L)")
        self._btn_rect   = btn("▭",   "Retângulo (R)")
        self._btn_circle = btn("○",   "Elipse (E)")
        self._btn_eraser = btn("🧹",  "Borracha (X)")
        self._btn_laser  = btn("🔴",  "Ponteiro Laser (S)")

        self._tool_buttons = [
            self._btn_pen, self._btn_hl, self._btn_line,
            self._btn_rect, self._btn_circle, self._btn_eraser, self._btn_laser,
        ]
        self._btn_pen.setChecked(True)
        for b in self._tool_buttons:
            inner.addWidget(b)

        self._btn_pen.clicked.connect(lambda: self._select_tool("pen"))
        self._btn_hl.clicked.connect(lambda: self._select_tool("highlighter"))
        self._btn_line.clicked.connect(lambda: self._select_tool("line"))
        self._btn_rect.clicked.connect(lambda: self._select_tool("rect"))
        self._btn_circle.clicked.connect(lambda: self._select_tool("circle"))
        self._btn_eraser.clicked.connect(lambda: self._select_tool("eraser"))
        self._btn_laser.clicked.connect(lambda: self._select_tool("laser"))

        self._add_sep(inner)

        # ── Cor + tamanho ────────────────────────────────────────────────
        self._color_btn = btn("", "Cor (C)", False)
        self._update_color_button()
        self._color_btn.clicked.connect(self._pick_color)
        inner.addWidget(self._color_btn)

        size_lbl = QLabel("⬤")
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_lbl.setStyleSheet("color: white; font-size: 8px;")
        inner.addWidget(size_lbl)

        self._size_slider = QSlider(Qt.Orientation.Vertical)
        self._size_slider.setRange(1, 20)
        self._size_slider.setValue(self._cfg.get("size", 3))
        self._size_slider.setFixedHeight(80)
        self._size_slider.valueChanged.connect(self._overlay.set_size)
        inner.addWidget(self._size_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(inner)

        # ── Ações ─────────────────────────────────────────────────────────
        b_undo  = btn("↩", "Desfazer (Ctrl+Z)", False)
        b_redo  = btn("↪", "Refazer (Ctrl+Y)", False)
        b_clear = btn("🗑", "Limpar tela (Del)", False)
        b_undo.clicked.connect(self._overlay.undo)
        b_redo.clicked.connect(self._overlay.redo)
        b_clear.clicked.connect(self._overlay.clear)
        for w in (b_undo, b_redo, b_clear):
            inner.addWidget(w)

        # Screenshot
        self._btn_screenshot = btn("📷", "Screenshot (Ctrl+S)", False)
        self._btn_screenshot.clicked.connect(lambda: self._do_screenshot(clipboard=False))
        inner.addWidget(self._btn_screenshot)

        self._add_sep(inner)

        # ── Modos de fundo ────────────────────────────────────────────────
        self._btn_whiteboard = btn("⬜", "Quadro Branco (W)")
        self._btn_whiteboard.clicked.connect(self._toggle_whiteboard)
        inner.addWidget(self._btn_whiteboard)

        self._btn_spotlight = btn("🔦", "Spotlight (O)")
        self._btn_spotlight.clicked.connect(self._toggle_spotlight)
        inner.addWidget(self._btn_spotlight)

        self._radius_slider = QSlider(Qt.Orientation.Vertical)
        self._radius_slider.setRange(60, 400)
        self._radius_slider.setValue(150)
        self._radius_slider.setFixedHeight(70)
        self._radius_slider.setVisible(False)
        self._radius_slider.valueChanged.connect(self._overlay.set_spotlight_radius)
        inner.addWidget(self._radius_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(inner)

        # ── Lupa ─────────────────────────────────────────────────────────
        self._btn_magnifier = btn("🔍", "Lupa (M)")
        self._btn_magnifier.clicked.connect(self._toggle_magnifier)
        inner.addWidget(self._btn_magnifier)

        self._zoom_slider = QSlider(Qt.Orientation.Vertical)
        self._zoom_slider.setRange(2, 6)
        self._zoom_slider.setValue(self._cfg.get("magnifier_zoom", 3))
        self._zoom_slider.setFixedHeight(60)
        self._zoom_slider.setVisible(False)
        self._zoom_slider.valueChanged.connect(self._magnifier.set_zoom)
        inner.addWidget(self._zoom_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(inner)

        # ── Apresentação + Pausar ─────────────────────────────────────────
        self._btn_present = btn("🎬", "Modo Apresentação (F11)")
        self._btn_present.clicked.connect(self._toggle_presentation)
        inner.addWidget(self._btn_present)

        self._btn_toggle = btn("🖱", "Pausar desenho (Tab)")
        self._btn_toggle.clicked.connect(self._toggle_drawing)
        inner.addWidget(self._btn_toggle)

        layout.addWidget(self._container)

    # ── Config ────────────────────────────────────────────────────────────

    def _apply_config(self):
        self._select_tool(self._cfg.get("tool", "pen"))
        self._overlay.set_color(self._current_color)
        self._overlay.set_size(self._size_slider.value())
        self._magnifier.set_zoom(self._zoom_slider.value())

    def get_state(self) -> dict:
        pos = self.pos()
        return {
            "tool": self._overlay._tool,
            "color": self._current_color.name(),
            "size": self._size_slider.value(),
            "toolbar_pos": {"x": pos.x(), "y": pos.y()},
            "magnifier_zoom": self._zoom_slider.value(),
            "whiteboard": self._btn_whiteboard.isChecked(),
        }

    def set_tray(self, tray):
        self._tray = tray

    # ── Tool ──────────────────────────────────────────────────────────────

    def _select_tool(self, tool: str):
        for b in self._tool_buttons:
            b.setChecked(False)
        btn_map = {
            "pen": self._btn_pen, "highlighter": self._btn_hl,
            "line": self._btn_line, "rect": self._btn_rect,
            "circle": self._btn_circle, "eraser": self._btn_eraser,
            "laser": self._btn_laser,
        }
        if tool in btn_map:
            btn_map[tool].setChecked(True)
        self._overlay.set_tool(tool)

    # ── Color ──────────────────────────────────────────────────────────────

    def _pick_color(self):
        color = QColorDialog.getColor(self._current_color, self, "Escolher cor")
        if color.isValid():
            self._current_color = color
            self._overlay.set_color(color)
            self._update_color_button()

    def _update_color_button(self):
        px = QPixmap(24, 24)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setBrush(QBrush(self._current_color))
        p.setPen(Qt.GlobalColor.white)
        p.drawEllipse(2, 2, 20, 20)
        p.end()
        self._color_btn.setIcon(QIcon(px))

    # ── Screenshot ────────────────────────────────────────────────────────

    def _do_screenshot(self, clipboard: bool = False):
        import screenshot as sc
        sc.capture(self, tray_icon=self._tray, copy_to_clipboard=clipboard)

    # ── Toggles ───────────────────────────────────────────────────────────

    def _toggle_whiteboard(self, checked: bool):
        self._overlay.set_whiteboard(checked)

    def _toggle_spotlight(self, checked: bool):
        self._overlay.set_spotlight(checked)
        self._radius_slider.setVisible(checked)
        self.adjustSize()

    def _toggle_magnifier(self, checked: bool):
        self._magnifier.set_active(checked)
        self._zoom_slider.setVisible(checked)
        self.adjustSize()

    def _toggle_drawing(self, checked: bool):
        self._drawing_active = not checked
        self._overlay.set_active(self._drawing_active)
        self._btn_toggle.setText("🖱" if checked else "✏️")

    # ── Modo Apresentação ─────────────────────────────────────────────────

    def _toggle_presentation(self, checked: bool):
        self._presentation_mode = checked
        if checked:
            self._edge_timer.start()
            self._hide_timer.start()
        else:
            self._edge_timer.stop()
            self._hide_timer.stop()
            self.show()
            self.setWindowOpacity(1.0)

    def _presentation_auto_hide(self):
        if self._presentation_mode:
            self.setWindowOpacity(0.0)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _check_edge_reveal(self):
        if not self._presentation_mode:
            return
        cursor = QCursor.pos()
        toolbar_x = self.pos().x()
        # Revela se cursor está a até 60px da posição X da toolbar
        if abs(cursor.x() - toolbar_x) <= 60:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setWindowOpacity(1.0)
            self._hide_timer.start()  # reinicia o temporizador de ocultação

    def enterEvent(self, event):
        if self._presentation_mode:
            self._hide_timer.stop()
            self.setWindowOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._presentation_mode:
            self._hide_timer.start()
        super().leaveEvent(event)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _add_sep(layout):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: rgba(255,255,255,30); max-height: 1px;")
        layout.addWidget(sep)

    # ── Drag ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _event):
        self._drag_pos = None

    # ── Keyboard ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        mod = event.modifiers()

        ctrl = mod & Qt.KeyboardModifier.ControlModifier
        shift = mod & Qt.KeyboardModifier.ShiftModifier

        if ctrl:
            if key == Qt.Key.Key_Z:
                self._overlay.undo()
            elif key == Qt.Key.Key_Y:
                self._overlay.redo()
            elif key == Qt.Key.Key_S:
                self._do_screenshot(clipboard=bool(shift))
            return

        tool_keys = {
            Qt.Key.Key_P: "pen",
            Qt.Key.Key_H: "highlighter",
            Qt.Key.Key_L: "line",
            Qt.Key.Key_R: "rect",
            Qt.Key.Key_E: "circle",
            Qt.Key.Key_X: "eraser",
            Qt.Key.Key_S: "laser",
        }
        if key in tool_keys:
            self._select_tool(tool_keys[key])
        elif key == Qt.Key.Key_C:
            self._pick_color()
        elif key == Qt.Key.Key_Delete:
            self._overlay.clear()
        elif key == Qt.Key.Key_W:
            self._btn_whiteboard.toggle()
            self._toggle_whiteboard(self._btn_whiteboard.isChecked())
        elif key == Qt.Key.Key_O:
            self._btn_spotlight.toggle()
            self._toggle_spotlight(self._btn_spotlight.isChecked())
        elif key == Qt.Key.Key_M:
            self._btn_magnifier.toggle()
            self._toggle_magnifier(self._btn_magnifier.isChecked())
        elif key == Qt.Key.Key_F11:
            self._btn_present.toggle()
            self._toggle_presentation(self._btn_present.isChecked())
        elif key == Qt.Key.Key_Tab:
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
