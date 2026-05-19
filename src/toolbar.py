from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QColorDialog, QLabel, QFrame
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter, QBrush


class ToolbarWindow(QWidget):
    """Barra de ferramentas flutuante, arrastável, sempre no topo."""

    def __init__(self, overlay):
        super().__init__()
        self._overlay = overlay
        self._drag_pos: QPoint | None = None
        self._current_color = QColor("#FF0000")
        self._drawing_active = True

        self._setup_window()
        self._build_ui()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(56)
        self.move(20, 200)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)

        # Container com fundo semi-transparente
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
            QPushButton:hover { background: rgba(255,255,255,30); }
            QPushButton:checked { background: rgba(255,255,255,60); border: 1px solid rgba(255,255,255,80); }
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

        def btn(icon: str, tooltip: str) -> QPushButton:
            b = QPushButton(icon)
            b.setToolTip(tooltip)
            b.setCheckable(True)
            return b

        self._btn_pen = btn("✏️", "Caneta (P)")
        self._btn_hl  = btn("🖊", "Marcador (H)")
        self._btn_line = btn("╱", "Linha (L)")
        self._btn_rect = btn("▭", "Retângulo (R)")
        self._btn_circle = btn("○", "Elipse (E)")
        self._btn_eraser = btn("🧹", "Borracha (X)")

        self._tool_buttons = [
            self._btn_pen, self._btn_hl, self._btn_line,
            self._btn_rect, self._btn_circle, self._btn_eraser,
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

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: rgba(255,255,255,30); max-height: 1px;")
        inner.addWidget(sep)

        # Seletor de cor
        self._color_btn = QPushButton()
        self._color_btn.setToolTip("Cor (C)")
        self._color_btn.setCheckable(False)
        self._update_color_button()
        self._color_btn.clicked.connect(self._pick_color)
        inner.addWidget(self._color_btn)

        # Slider de tamanho
        size_label = QLabel("⬤")
        size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_label.setStyleSheet("color: white; font-size: 8px;")
        inner.addWidget(size_label)

        self._size_slider = QSlider(Qt.Orientation.Vertical)
        self._size_slider.setRange(1, 20)
        self._size_slider.setValue(3)
        self._size_slider.setFixedHeight(80)
        self._size_slider.valueChanged.connect(self._overlay.set_size)
        inner.addWidget(self._size_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background: rgba(255,255,255,30); max-height: 1px;")
        inner.addWidget(sep2)

        # Ações
        btn_undo = QPushButton("↩")
        btn_undo.setCheckable(False)
        btn_undo.setToolTip("Desfazer (Ctrl+Z)")
        btn_undo.clicked.connect(self._overlay.undo)
        inner.addWidget(btn_undo)

        btn_redo = QPushButton("↪")
        btn_redo.setCheckable(False)
        btn_redo.setToolTip("Refazer (Ctrl+Y)")
        btn_redo.clicked.connect(self._overlay.redo)
        inner.addWidget(btn_redo)

        btn_clear = QPushButton("🗑")
        btn_clear.setCheckable(False)
        btn_clear.setToolTip("Limpar tela (Del)")
        btn_clear.clicked.connect(self._overlay.clear)
        inner.addWidget(btn_clear)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("background: rgba(255,255,255,30); max-height: 1px;")
        inner.addWidget(sep3)

        self._btn_toggle = QPushButton("🖱")
        self._btn_toggle.setCheckable(True)
        self._btn_toggle.setToolTip("Pausar desenho (Tab)")
        self._btn_toggle.clicked.connect(self._toggle_drawing)
        inner.addWidget(self._btn_toggle)

        layout.addWidget(self._container)

    # ── Tool / color helpers ──────────────────────────────────────────────

    def _select_tool(self, tool: str):
        for b in self._tool_buttons:
            b.setChecked(False)
        btn_map = {
            "pen": self._btn_pen, "highlighter": self._btn_hl,
            "line": self._btn_line, "rect": self._btn_rect,
            "circle": self._btn_circle, "eraser": self._btn_eraser,
        }
        btn_map[tool].setChecked(True)
        self._overlay.set_tool(tool)

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

    def _toggle_drawing(self, checked: bool):
        self._drawing_active = not checked
        self._overlay.set_active(self._drawing_active)
        self._btn_toggle.setText("🖱" if checked else "✏️")

    # ── Drag window ───────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _event):
        self._drag_pos = None

    # ── Keyboard shortcuts ────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_P:
            self._select_tool("pen")
        elif key == Qt.Key.Key_H:
            self._select_tool("highlighter")
        elif key == Qt.Key.Key_L:
            self._select_tool("line")
        elif key == Qt.Key.Key_R:
            self._select_tool("rect")
        elif key == Qt.Key.Key_E:
            self._select_tool("circle")
        elif key == Qt.Key.Key_X:
            self._select_tool("eraser")
        elif key == Qt.Key.Key_C:
            self._pick_color()
        elif key == Qt.Key.Key_Delete:
            self._overlay.clear()
        elif key == Qt.Key.Key_Tab:
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                self._overlay.undo()
            elif key == Qt.Key.Key_Y:
                self._overlay.redo()
