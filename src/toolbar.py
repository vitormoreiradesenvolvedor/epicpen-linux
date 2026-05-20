from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QColorDialog, QFrame, QLayout,
)
from PyQt6.QtCore import Qt, QPoint, QTimer, QSize, QEvent
from PyQt6.QtGui import QColor, QCursor, QGuiApplication

import icons
from hotkeys import GlobalHotkeyListener
from magnifier import MagnifierWindow

_ICON = QSize(22, 22)
_BTN  = 36   # button side (px)
_W    = 56   # toolbar width (expanded e collapsed usam a mesma largura)

_STYLE = """
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
"""


class ToolbarWindow(QWidget):
    """Barra de ferramentas flutuante, colapsável, sempre no topo."""

    def __init__(self, overlay, config: dict | None = None):
        super().__init__()
        self._overlay   = overlay
        self._collapsed = False
        # drag state — controlado pelo eventFilter instalado em todos os filhos
        self._drag_start = None   # QPoint global quando o botão foi pressionado
        self._dragging   = False  # True após exceder o threshold (startSystemMove)
        self._drawing_active = True
        self._magnifier = MagnifierWindow()
        self._cfg       = config or {}
        self._tray      = None

        color_hex = self._cfg.get("color", "#FF0000")
        self._current_color = QColor(color_hex)

        self._presentation_mode = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(1500)
        self._hide_timer.timeout.connect(self._presentation_auto_hide)
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(150)
        self._edge_timer.timeout.connect(self._check_edge_reveal)

        self._setup_window()
        self._build_ui()
        self._install_event_filters()
        self._apply_config()

        self._hotkeys = GlobalHotkeyListener(self)
        self._hotkeys.toggled.connect(self._on_global_hotkey)
        self._hotkeys.start()

        # Sempre no topo: requestActivate() é o único mecanismo que funciona
        # no Wayland nativo (raise_() é no-op em xdg-shell).
        self._raise_timer = QTimer(self)
        self._raise_timer.setInterval(500)
        self._raise_timer.timeout.connect(self._reaffirm_top)
        self._raise_timer.start()
        QGuiApplication.instance().focusWindowChanged.connect(
            lambda w: QTimer.singleShot(80, self._reaffirm_top)
            if w is not None else None
        )

    # ── Window setup ──────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(_W)
        pos = self._cfg.get("toolbar_pos", {"x": 20, "y": 150})
        self.move(pos.get("x", 20), pos.get("y", 150))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 8, 4, 8)
        root.setSpacing(0)
        # Bloqueia resize externo (compositor não pode redimensionar)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self._container = QFrame(self)
        self._container.setObjectName("toolbar")
        self._container.setStyleSheet(_STYLE)

        outer = QVBoxLayout(self._container)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        # Logo button — visible only when collapsed
        # _W=56, margens totais=20 → inner=36=_BTN → logo centrado perfeitamente
        self._logo_btn = QPushButton()
        self._logo_btn.setIcon(icons.logo())
        self._logo_btn.setIconSize(QSize(30, 30))
        self._logo_btn.setFixedSize(_BTN, _BTN)
        self._logo_btn.setToolTip("Expandir")
        self._logo_btn.setCheckable(False)
        self._logo_btn.setVisible(False)
        self._logo_btn.clicked.connect(self._do_expand)
        outer.addWidget(self._logo_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Full content — visible when expanded
        self._expanded_widget = self._build_expanded()
        outer.addWidget(self._expanded_widget)

        root.addWidget(self._container)

    def _mk_btn(self, tooltip: str, checkable: bool = True) -> QPushButton:
        b = QPushButton()
        b.setToolTip(tooltip)
        b.setCheckable(checkable)
        b.setFixedSize(_BTN, _BTN)
        b.setIconSize(_ICON)
        return b

    def _build_expanded(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # Header: collapse button (full width — drag works from anywhere via eventFilter)
        col_btn = QPushButton()
        col_btn.setIcon(icons.collapse_left())
        col_btn.setIconSize(_ICON)
        col_btn.setFixedSize(_BTN, 28)
        col_btn.setToolTip("Recolher")
        col_btn.setCheckable(False)
        col_btn.clicked.connect(self._do_collapse)
        lay.addWidget(col_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Drawing tools
        self._btn_pen    = self._mk_btn("Caneta (P)")
        self._btn_pen.setIcon(icons.pen())
        self._btn_hl     = self._mk_btn("Marcador (H)")
        self._btn_hl.setIcon(icons.highlighter())
        self._btn_line   = self._mk_btn("Linha (L)")
        self._btn_line.setIcon(icons.line())
        self._btn_rect   = self._mk_btn("Retângulo (R)")
        self._btn_rect.setIcon(icons.rect())
        self._btn_circle = self._mk_btn("Elipse (E)")
        self._btn_circle.setIcon(icons.circle())
        self._btn_eraser = self._mk_btn("Borracha (X)")
        self._btn_eraser.setIcon(icons.eraser())
        self._btn_laser  = self._mk_btn("Ponteiro Laser (S)")
        self._btn_laser.setIcon(icons.laser())

        self._tool_buttons = [
            self._btn_pen, self._btn_hl, self._btn_line,
            self._btn_rect, self._btn_circle, self._btn_eraser, self._btn_laser,
        ]
        self._btn_pen.setChecked(True)
        for b in self._tool_buttons:
            lay.addWidget(b)

        self._btn_pen.clicked.connect(lambda: self._select_tool("pen"))
        self._btn_hl.clicked.connect(lambda: self._select_tool("highlighter"))
        self._btn_line.clicked.connect(lambda: self._select_tool("line"))
        self._btn_rect.clicked.connect(lambda: self._select_tool("rect"))
        self._btn_circle.clicked.connect(lambda: self._select_tool("circle"))
        self._btn_eraser.clicked.connect(lambda: self._select_tool("eraser"))
        self._btn_laser.clicked.connect(lambda: self._select_tool("laser"))

        self._add_sep(lay)

        # Color + size
        self._color_btn = self._mk_btn("Cor (C)", checkable=False)
        self._update_color_button()
        self._color_btn.clicked.connect(self._pick_color)
        lay.addWidget(self._color_btn)

        self._size_slider = QSlider(Qt.Orientation.Vertical)
        self._size_slider.setRange(1, 20)
        self._size_slider.setValue(self._cfg.get("size", 3))
        self._size_slider.setFixedHeight(80)
        self._size_slider.valueChanged.connect(self._overlay.set_size)
        lay.addWidget(self._size_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Actions
        b_undo  = self._mk_btn("Desfazer (Ctrl+Z)", checkable=False)
        b_undo.setIcon(icons.undo())
        b_redo  = self._mk_btn("Refazer (Ctrl+Y)", checkable=False)
        b_redo.setIcon(icons.redo())
        b_clear = self._mk_btn("Limpar tela (Del)", checkable=False)
        b_clear.setIcon(icons.trash())
        b_undo.clicked.connect(self._overlay.undo)
        b_redo.clicked.connect(self._overlay.redo)
        b_clear.clicked.connect(self._overlay.clear)
        for b in (b_undo, b_redo, b_clear):
            lay.addWidget(b)

        self._btn_screenshot = self._mk_btn("Screenshot (Ctrl+S)", checkable=False)
        self._btn_screenshot.setIcon(icons.screenshot())
        self._btn_screenshot.clicked.connect(lambda: self._do_screenshot(clipboard=False))
        lay.addWidget(self._btn_screenshot)

        self._add_sep(lay)

        # Mode toggles
        self._btn_whiteboard = self._mk_btn("Quadro Branco (W)")
        self._btn_whiteboard.setIcon(icons.whiteboard())
        self._btn_whiteboard.clicked.connect(self._toggle_whiteboard)
        lay.addWidget(self._btn_whiteboard)

        self._btn_spotlight = self._mk_btn("Spotlight (O)")
        self._btn_spotlight.setIcon(icons.spotlight())
        self._btn_spotlight.clicked.connect(self._toggle_spotlight)
        lay.addWidget(self._btn_spotlight)

        self._radius_slider = QSlider(Qt.Orientation.Vertical)
        self._radius_slider.setRange(60, 400)
        self._radius_slider.setValue(150)
        self._radius_slider.setFixedHeight(70)
        self._radius_slider.setVisible(False)
        self._radius_slider.valueChanged.connect(self._overlay.set_spotlight_radius)
        lay.addWidget(self._radius_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Magnifier
        self._btn_magnifier = self._mk_btn("Lupa (M)")
        self._btn_magnifier.setIcon(icons.magnifier())
        self._btn_magnifier.clicked.connect(self._toggle_magnifier)
        lay.addWidget(self._btn_magnifier)

        self._zoom_slider = QSlider(Qt.Orientation.Vertical)
        self._zoom_slider.setRange(2, 6)
        self._zoom_slider.setValue(self._cfg.get("magnifier_zoom", 3))
        self._zoom_slider.setFixedHeight(60)
        self._zoom_slider.setVisible(False)
        self._zoom_slider.valueChanged.connect(self._magnifier.set_zoom)
        lay.addWidget(self._zoom_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Presentation + drawing toggle
        self._btn_present = self._mk_btn("Modo Apresentação (F11)")
        self._btn_present.setIcon(icons.presentation())
        self._btn_present.clicked.connect(self._toggle_presentation)
        lay.addWidget(self._btn_present)

        self._btn_toggle = self._mk_btn(
            "Pausar/retomar desenho\nClic esquerdo | Tab | Botão direito na toolbar"
        )
        self._btn_toggle.setIcon(icons.mouse_pause())
        self._btn_toggle.clicked.connect(self._toggle_drawing)
        lay.addWidget(self._btn_toggle)

        return w

    # ── Collapse / expand ─────────────────────────────────────────────────────

    def _do_collapse(self):
        self._collapsed = True
        self._expanded_widget.setVisible(False)
        self._logo_btn.setVisible(True)
        self.setFixedWidth(_W)
        self.adjustSize()
        # Colapso pausa o desenho (como no EpicPen original)
        self._btn_toggle.setChecked(True)
        self._toggle_drawing(True)
        QTimer.singleShot(0, self._sync_overlay_mask)

    def _do_expand(self):
        self._collapsed = False
        self._logo_btn.setVisible(False)
        self._expanded_widget.setVisible(True)
        self.setFixedWidth(_W)
        self.adjustSize()
        # Expansão retoma o desenho automaticamente
        self._btn_toggle.setChecked(False)
        self._toggle_drawing(False)
        QTimer.singleShot(0, self._sync_overlay_mask)

    # ── Config ────────────────────────────────────────────────────────────────

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

    def closeEvent(self, event):
        self._raise_timer.stop()
        self._hotkeys.stop()
        super().closeEvent(event)

    def _on_global_hotkey(self):
        """Chamado pelo listener global — seguro para uso entre threads."""
        self._btn_toggle.toggle()
        self._toggle_drawing(self._btn_toggle.isChecked())
        # Hotkey também traz toolbar ao topo (substitui o alt+tab do usuário)
        self._reaffirm_top()

    def _reaffirm_top(self):
        """
        Traz a toolbar ao topo do z-stack.
        raise_() é no-op no Wayland nativo; requestActivate() via xdg-activation
        é o mecanismo correto para pedir ao compositor que eleve a janela.
        """
        self.raise_()
        handle = self.windowHandle()
        if handle:
            handle.requestActivate()

    # ── Tool selection ────────────────────────────────────────────────────────

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

    # ── Color ─────────────────────────────────────────────────────────────────

    def _pick_color(self):
        color = QColorDialog.getColor(self._current_color, self, "Escolher cor")
        if color.isValid():
            self._current_color = color
            self._overlay.set_color(color)
            self._update_color_button()

    def _update_color_button(self):
        self._color_btn.setIcon(icons.color_dot(self._current_color))

    # ── Screenshot ────────────────────────────────────────────────────────────

    def _do_screenshot(self, clipboard: bool = False):
        import screenshot as sc
        sc.capture(self, tray_icon=self._tray, copy_to_clipboard=clipboard)

    # ── Mode toggles ──────────────────────────────────────────────────────────

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
        self._btn_toggle.setIcon(icons.mouse_active() if checked else icons.mouse_pause())

    # ── Presentation mode ─────────────────────────────────────────────────────

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
        if abs(cursor.x() - self.pos().x()) <= 60:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setWindowOpacity(1.0)
            self._hide_timer.start()

    def enterEvent(self, event):
        if self._presentation_mode:
            self._hide_timer.stop()
            self.setWindowOpacity(1.0)
        # Wayland: puxa foco de teclado ao entrar na toolbar
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().enterEvent(event)

    def wheelEvent(self, event):
        self._btn_toggle.toggle()
        self._toggle_drawing(self._btn_toggle.isChecked())
        event.accept()

    def leaveEvent(self, event):
        if self._presentation_mode:
            self._hide_timer.start()
        super().leaveEvent(event)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _add_sep(layout):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: rgba(255,255,255,30); max-height: 1px;")
        layout.addWidget(sep)

    # ── Overlay mask sync ─────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self._sync_overlay_mask)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_mask()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_overlay_mask()

    def _sync_overlay_mask(self):
        self._overlay.set_toolbar_region(self.geometry())

    # ── Drag + event filter ───────────────────────────────────────────────────

    def _install_event_filters(self):
        """Instala event filter em toda a árvore de widgets filhos do container."""
        self._container.installEventFilter(self)
        for w in self._container.findChildren(QWidget):
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        t = event.type()

        # Tab antes do sistema de foco consumir
        if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
            return True

        # Botão direito em qualquer filho → toggle
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
            return True

        # Sliders gerenciam o próprio drag — não interferir
        if isinstance(obj, QSlider):
            return False

        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._dragging   = False
            return False  # não consome — deixa o botão ativar normalmente

        if t == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._drag_start is not None and not self._dragging:
                delta = event.globalPosition().toPoint() - self._drag_start
                if abs(delta.x()) + abs(delta.y()) >= 6:
                    self._dragging = True
                    handle = self.windowHandle()
                    if handle:
                        # Delega o drag ao compositor (funciona em Wayland e X11)
                        handle.startSystemMove()
                    return True
            if self._dragging:
                return True  # consome moves subsequentes enquanto compositor draga

        if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_dragging     = self._dragging
            self._drag_start = None
            self._dragging   = False
            if was_dragging:
                return True  # consome release → botão não dispara click após drag

        return False  # passa todos os demais eventos adiante

    # ── Tab override via event() (segunda camada de segurança) ────────────────

    def event(self, event):
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Tab):
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
            return True
        return super().event(event)

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        mod = event.modifiers()
        ctrl  = mod & Qt.KeyboardModifier.ControlModifier
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
