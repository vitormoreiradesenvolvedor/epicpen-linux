from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QColorDialog, QFrame, QLayout,
    QDialog, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QFontComboBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QPoint, QTimer, QSize, QEvent
from PyQt6.QtGui import QColor, QCursor

import icons
import layershell
from hotkeys import GlobalHotkeyListener
from magnifier import MagnifierWindow

_ICON = QSize(16, 16)
_BTN  = 25   # button side (px) — 30% menor que o original 36px
_W    = 56   # toolbar width (expanded e collapsed usam a mesma largura)

_STYLE = """
    QFrame#toolbar {
        background-color: rgba(30, 30, 30, 220);
        border-radius: 9px;
        border: 1px solid rgba(255,255,255,30);
    }
    QPushButton {
        background: transparent;
        border: none;
        border-radius: 4px;
        color: white;
        padding: 3px;
        min-width: 25px;
        min-height: 25px;
    }
    QPushButton:hover  { background: rgba(255,255,255,30); }
    QPushButton:checked {
        background: rgba(255,255,255,60);
        border: 1px solid rgba(255,255,255,80);
    }
    QSlider::groove:vertical {
        background: rgba(255,255,255,40);
        width: 4px; border-radius: 2px;
        margin: 6px 0px;
    }
    QSlider::handle:vertical {
        background: white;
        width: 12px; height: 12px;
        margin: -4px -4px;
        border-radius: 6px;
    }
"""

# Estilo colapsado: frame transparente, sem borda — só o ícone fica visível
_STYLE_COLLAPSED = """
    QFrame#toolbar { background: transparent; border: none; }
    QPushButton {
        background: transparent; border: none; border-radius: 4px;
        padding: 3px; min-width: 25px; min-height: 25px;
    }
    QPushButton:hover { background: rgba(255,255,255,20); }
"""


class TextDialog(QDialog):
    """Diálogo para configurar texto antes de inserir na tela."""

    def __init__(self, default_color, parent=None, *,
                 initial_text: str = "",
                 initial_font: str = "",
                 initial_size: int = 24):
        super().__init__(parent)
        self.setWindowTitle("Inserir Texto")
        self._color = QColor(default_color)

        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        lay.addWidget(QLabel("Texto: (Ctrl+Enter para confirmar)"))
        self._text_edit = QPlainTextEdit()
        self._text_edit.setMinimumWidth(250)
        self._text_edit.setMinimumHeight(80)
        if initial_text:
            self._text_edit.setPlainText(initial_text)
        lay.addWidget(self._text_edit)

        row_font = QHBoxLayout()
        row_font.addWidget(QLabel("Fonte:"))
        self._font_combo = QFontComboBox()
        if initial_font:
            from PyQt6.QtGui import QFont as _QFont
            self._font_combo.setCurrentFont(_QFont(initial_font))
        else:
            self._font_combo.setCurrentFont(self._font_combo.currentFont())
        row_font.addWidget(self._font_combo)
        lay.addLayout(row_font)

        row_size = QHBoxLayout()
        row_size.addWidget(QLabel("Tamanho:"))
        self._size_spin = QSpinBox()
        self._size_spin.setRange(6, 144)
        self._size_spin.setValue(max(6, min(144, int(round(initial_size)))))
        row_size.addWidget(self._size_spin)
        lay.addLayout(row_size)

        row_color = QHBoxLayout()
        row_color.addWidget(QLabel("Cor:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(28, 22)
        self._update_color_preview()
        self._color_btn.clicked.connect(self._pick_color)
        row_color.addWidget(self._color_btn)
        row_color.addStretch()
        lay.addLayout(row_color)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._text_edit.installEventFilter(self)

    def _pick_color(self):
        if self.windowFlags() & Qt.WindowType.Popup:
            dlg = QColorDialog(self._color, self)
            dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            dlg.setWindowFlags(Qt.WindowType.Popup)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                c = dlg.currentColor()
                if c.isValid():
                    self._color = c
                    self._update_color_preview()
        else:
            c = QColorDialog.getColor(self._color, self, "Cor do texto")
            if c.isValid():
                self._color = c
                self._update_color_preview()

    def eventFilter(self, obj, event):
        if (obj is self._text_edit
                and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.accept()
            return True
        return super().eventFilter(obj, event)

    def _update_color_preview(self):
        self._color_btn.setStyleSheet(
            f"background:{self._color.name()}; border:1px solid #888;"
        )

    def text(self) -> str:
        return self._text_edit.toPlainText()

    def font_family(self) -> str:
        return self._font_combo.currentFont().family()

    def font_size(self) -> int:
        return self._size_spin.value()

    def color(self):
        return QColor(self._color)


class ToolbarWindow(QWidget):
    """Barra de ferramentas flutuante, colapsável, sempre no topo."""

    def __init__(self, overlay, config: dict | None = None):
        super().__init__()
        self._overlay   = overlay
        self._collapsed = False
        # drag state — controlado pelo eventFilter instalado em todos os filhos
        self._drag_start        = None   # scenePos no press (threshold)
        self._drag_start_screen = None   # cursor em coords de ecrã no press
        self._drag_start_pos    = None   # _lsw_pos snapshot no início do drag
        self._dragging          = False  # True após exceder o threshold
        self._lsw_ptr           = None   # LayerShellQt::Window* se layer-shell ativo
        self._drawing_active    = True
        self._passthrough_active = False  # True quando no modo seta (visível + sem input)
        self._magnifier = MagnifierWindow()
        self._cfg       = config or {}
        self._tray      = None

        # Posição válida da toolbar em coordenadas layer-shell (margens left, top).
        # Inicializada do config; atualizada apenas por drag concluído.
        # Nunca usar widget.pos() / mapToGlobal() — infiáveis no Wayland.
        _p = self._cfg.get("toolbar_pos", {"x": 20, "y": 150})
        self._lsw_pos = QPoint(_p.get("x", 20), _p.get("y", 150))
        self._current_screen = None  # inicializado em show() após layer-shell configurado

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

        overlay.text_placement_requested.connect(self._on_text_placement_requested)
        overlay.text_edit_requested.connect(self._on_text_edit_requested)

        # Tooltip interno: QLabel filho da janela, posicionado à direita da coluna
        # de botões. Não depende de popup Qt — funciona em wlr-layer-shell.
        self._tt_label = QLabel("", self)
        self._tt_label.setStyleSheet(
            "background:#1e1e1e; color:white;"
            "border:1px solid rgba(255,255,255,60);"
            "border-radius:4px; padding:3px 8px; font-size:12px;"
        )
        self._tt_label.hide()
        self._tt_timer = QTimer(self)
        self._tt_timer.setSingleShot(True)
        self._tt_timer.setInterval(1000)
        self._tt_timer.timeout.connect(self._fire_tooltip)
        self._tt_widget: "QPushButton | None" = None


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
        root.setContentsMargins(4, 5, 4, 5)
        root.setSpacing(0)
        # Bloqueia resize externo (compositor não pode redimensionar)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._root_layout = root

        self._container = QFrame(self)
        self._container.setObjectName("toolbar")
        self._container.setStyleSheet(_STYLE)
        # Largura fixa do container: nunca expande quando a janela cresce para tooltip
        self._container.setFixedWidth(_W - 8)  # _W - margens root (4+4)

        outer = QVBoxLayout(self._container)
        outer.setContentsMargins(5, 5, 5, 5)
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
        lay.setSpacing(2)

        # Header: collapse button (full width — drag works from anywhere via eventFilter)
        col_btn = QPushButton()
        col_btn.setIcon(icons.collapse_left())
        col_btn.setIconSize(_ICON)
        col_btn.setFixedSize(_BTN, 20)
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
        self._btn_drag   = self._mk_btn("Arrastar (G)")
        self._btn_drag.setIcon(icons.drag_tool())
        self._btn_text   = self._mk_btn("Texto (T)")
        self._btn_text.setIcon(icons.text_tool())

        self._tool_buttons = [
            self._btn_pen, self._btn_hl, self._btn_line,
            self._btn_rect, self._btn_circle, self._btn_eraser,
            self._btn_laser, self._btn_drag, self._btn_text,
        ]
        self._btn_pen.setChecked(True)
        for b in self._tool_buttons:
            lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._btn_pen.clicked.connect(lambda c: self._select_tool("pen") if c else self._activate_arrow_mode())
        self._btn_hl.clicked.connect(lambda c: self._select_tool("highlighter") if c else self._activate_arrow_mode())
        self._btn_line.clicked.connect(lambda c: self._select_tool("line") if c else self._activate_arrow_mode())
        self._btn_rect.clicked.connect(lambda c: self._select_tool("rect") if c else self._activate_arrow_mode())
        self._btn_circle.clicked.connect(lambda c: self._select_tool("circle") if c else self._activate_arrow_mode())
        self._btn_eraser.clicked.connect(lambda c: self._select_tool("eraser") if c else self._activate_arrow_mode())
        self._btn_laser.clicked.connect(lambda c: self._select_tool("laser") if c else self._activate_arrow_mode())
        self._btn_drag.clicked.connect(lambda c: self._select_tool("drag") if c else self._activate_arrow_mode())
        self._btn_text.clicked.connect(lambda c: self._select_tool("text") if c else self._activate_arrow_mode())

        self._add_sep(lay)

        # Color + size
        self._color_btn = self._mk_btn("Cor (C)", checkable=False)
        self._update_color_button()
        self._color_btn.clicked.connect(self._pick_color)
        lay.addWidget(self._color_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._size_slider = QSlider(Qt.Orientation.Vertical)
        self._size_slider.setRange(1, 20)
        self._size_slider.setValue(self._cfg.get("size", 3))
        self._size_slider.setFixedHeight(56)
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
            lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._btn_screenshot = self._mk_btn("Screenshot (Ctrl+S)", checkable=False)
        self._btn_screenshot.setIcon(icons.screenshot())
        self._btn_screenshot.clicked.connect(lambda: self._do_screenshot(clipboard=False))
        lay.addWidget(self._btn_screenshot, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Mode toggles
        self._btn_whiteboard = self._mk_btn("Quadro Branco (W)")
        self._btn_whiteboard.setIcon(icons.whiteboard())
        self._btn_whiteboard.clicked.connect(self._toggle_whiteboard)
        lay.addWidget(self._btn_whiteboard, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._wb_bg_color = QColor(255, 255, 255)
        self._btn_wb_bg = self._mk_btn("Cor do fundo do quadro", checkable=False)
        self._btn_wb_bg.setIcon(icons.color_dot(self._wb_bg_color))
        self._btn_wb_bg.setVisible(False)
        self._btn_wb_bg.clicked.connect(self._pick_whiteboard_bg)
        lay.addWidget(self._btn_wb_bg, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._btn_spotlight = self._mk_btn("Spotlight (O)")
        self._btn_spotlight.setIcon(icons.spotlight())
        self._btn_spotlight.clicked.connect(self._toggle_spotlight)
        lay.addWidget(self._btn_spotlight, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._radius_slider = QSlider(Qt.Orientation.Vertical)
        self._radius_slider.setRange(60, 400)
        self._radius_slider.setValue(150)
        self._radius_slider.setFixedHeight(49)
        self._radius_slider.setVisible(False)
        self._radius_slider.valueChanged.connect(self._overlay.set_spotlight_radius)
        lay.addWidget(self._radius_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Magnifier
        self._btn_magnifier = self._mk_btn("Lupa (M)")
        self._btn_magnifier.setIcon(icons.magnifier())
        self._btn_magnifier.clicked.connect(self._toggle_magnifier)
        lay.addWidget(self._btn_magnifier, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._zoom_slider = QSlider(Qt.Orientation.Vertical)
        self._zoom_slider.setRange(2, 6)
        self._zoom_slider.setValue(self._cfg.get("magnifier_zoom", 3))
        self._zoom_slider.setFixedHeight(42)
        self._zoom_slider.setVisible(False)
        self._zoom_slider.valueChanged.connect(self._magnifier.set_zoom)
        lay.addWidget(self._zoom_slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        # Presentation + drawing toggle
        self._btn_present = self._mk_btn("Modo Apresentação (F11)")
        self._btn_present.setIcon(icons.presentation())
        self._btn_present.clicked.connect(self._toggle_presentation)
        lay.addWidget(self._btn_present, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._btn_toggle = self._mk_btn(
            "Modo seta: desenhos visíveis, interaja com apps abaixo"
        )
        self._btn_toggle.setIcon(icons.mouse_pause())
        self._btn_toggle.clicked.connect(self._toggle_passthrough)
        lay.addWidget(self._btn_toggle, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._add_sep(lay)

        self._btn_exit = self._mk_btn("Sair", checkable=False)
        self._btn_exit.setIcon(icons.exit_btn())
        self._btn_exit.clicked.connect(self._quit_app)
        lay.addWidget(self._btn_exit, alignment=Qt.AlignmentFlag.AlignHCenter)

        return w

    # ── Collapse / expand ─────────────────────────────────────────────────────

    def _do_collapse(self):
        self._collapsed = True
        self._expanded_widget.setVisible(False)
        self._logo_btn.setVisible(True)
        self._container.setStyleSheet(_STYLE_COLLAPSED)  # sem borda/fundo
        self.setFixedWidth(_W)
        self.adjustSize()
        # Colapso pausa o desenho (como no EpicPen original)
        self._btn_toggle.setChecked(True)
        self._toggle_drawing(True)
        # Em modo apresentação o ícone colapsado deve permanecer visível
        if self._presentation_mode:
            self._hide_timer.stop()
            self.setWindowOpacity(1.0)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        QTimer.singleShot(0, self._sync_overlay_mask)
        QTimer.singleShot(0, self._update_input_region)

    def _do_expand(self):
        self._collapsed = False
        self._logo_btn.setVisible(False)
        self._expanded_widget.setVisible(True)
        self._container.setStyleSheet(_STYLE)  # restaura fundo escuro
        self.setFixedWidth(_W)
        self.adjustSize()
        # Re-clamp: a altura expandida pode ultrapassar o fundo da área disponível.
        clamped = self._clamp_pos(self._lsw_pos)
        if clamped != self._lsw_pos:
            self._lsw_pos = clamped
            if self._lsw_ptr:
                scr = self._current_screen
                origin = scr.geometry().topLeft() if scr else QPoint(0, 0)
                layershell.move_to(self._lsw_ptr, (clamped - origin).x(), (clamped - origin).y())
            else:
                self.move(clamped)
        # Expansão retoma o desenho automaticamente
        self._btn_toggle.setChecked(False)
        self._toggle_drawing(False)
        QTimer.singleShot(0, self._sync_overlay_mask)
        QTimer.singleShot(0, self._update_input_region)
        # Em modo apresentação retoma o timer de auto-esconder após expandir
        if self._presentation_mode:
            self._hide_timer.start()

    def _update_input_region(self):
        """Restringe input ao botão logo quando colapsado; sem máscara ao expandir."""
        from PyQt6.QtGui import QRegion
        if self._collapsed:
            # Converte geometria do logo para coords da janela toolbar
            logo_geo = self._logo_btn.geometry().translated(
                self._container.geometry().topLeft()
            )
            self.setMask(QRegion(logo_geo))
        else:
            self.clearMask()

    # ── Config ────────────────────────────────────────────────────────────────

    def _apply_config(self):
        self._select_tool("pen")
        self._overlay.set_color(self._current_color)
        self._overlay.set_size(self._size_slider.value())
        self._magnifier.set_zoom(self._zoom_slider.value())

    def get_state(self) -> dict:
        return {
            "color": self._current_color.name(),
            "size": self._size_slider.value(),
            "toolbar_pos": {"x": self._lsw_pos.x(), "y": self._lsw_pos.y()},
            "magnifier_zoom": self._zoom_slider.value(),
        }

    def set_tray(self, tray):
        self._tray = tray

    def closeEvent(self, event):
        self._hotkeys.stop()
        super().closeEvent(event)

    def _on_global_hotkey(self):
        """
        Tab global: sempre traz a toolbar para frente.
        - Se recolhida → expande
        - Se desenho pausado → retoma
        - Re-aplica keepAbove via KWin para garantir z-order
        """
        if self._collapsed:
            self._do_expand()
        elif self._passthrough_active:
            self._btn_toggle.setChecked(False)
            self._toggle_passthrough(False)
        elif not self._drawing_active:
            self._btn_toggle.setChecked(False)
            self._toggle_drawing(False)
        import keepabove
        keepabove.set_keepabove()
        self._reaffirm_top()

    def _reaffirm_top(self):
        self.raise_()
        self.activateWindow()
        if self._drawing_active:
            self._overlay.raise_()

    # ── Tool selection ────────────────────────────────────────────────────────

    def _activate_arrow_mode(self):
        """Clique num item já ativo: desmarca tudo e entra em modo seta (passthrough)."""
        self._btn_toggle.setChecked(True)
        self._toggle_passthrough(True)

    def _select_tool(self, tool: str):
        # Ao selecionar ferramenta, sai de qualquer modo de pausa
        if self._passthrough_active:
            self._btn_toggle.setChecked(False)
            self._toggle_passthrough(False)
        elif self._btn_toggle.isChecked():
            self._btn_toggle.setChecked(False)
            self._drawing_active = True
            self._overlay.set_active(True)
            self._btn_toggle.setIcon(icons.mouse_pause())
        for b in self._tool_buttons:
            b.setChecked(False)
        btn_map = {
            "pen": self._btn_pen, "highlighter": self._btn_hl,
            "line": self._btn_line, "rect": self._btn_rect,
            "circle": self._btn_circle, "eraser": self._btn_eraser,
            "laser": self._btn_laser, "drag": self._btn_drag, "text": self._btn_text,
        }
        if tool in btn_map:
            btn_map[tool].setChecked(True)
        self._overlay.set_tool(tool)

    # ── Dialog helpers ────────────────────────────────────────────────────────

    def _pre_dialog(self) -> bool:
        """Prepara overlay para abrir diálogo. Retorna was_drawing.

        Layer-shell: diálogos abrem como xdg_popup (Qt::Popup) acima da superfície
        layer-shell — sem necessidade de manipular a camada do overlay.
        Embed/X11: pausa o overlay normalmente via _toggle_drawing.
        """
        was_drawing = self._drawing_active
        ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
        if was_drawing and not ov_lsw:
            self._btn_toggle.setChecked(True)
            self._toggle_drawing(True)
        return was_drawing

    def _post_dialog(self, was_drawing: bool):
        """Restaura overlay após fechar diálogo."""
        ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
        if was_drawing and not ov_lsw:
            self._btn_toggle.setChecked(False)
            self._toggle_drawing(False)

    # ── Text tool ─────────────────────────────────────────────────────────────

    def _on_text_placement_requested(self, pos):
        was_drawing = self._pre_dialog()
        ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
        # Em layer-shell, usa o overlay (full-screen) como pai do popup.
        # O overlay cobre o monitor desde (0,0), então pos é anchor_rect direto.
        # O toolbar (56px) como pai causaria clamp do compositor para o canto.
        dlg = TextDialog(self._current_color, parent=self._overlay if ov_lsw else self)
        if ov_lsw:
            dlg.setWindowFlags(Qt.WindowType.Popup)
            dlg.adjustSize()
            dlg.move(pos.x() + 8, pos.y() + 8)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._overlay.place_text(
                pos, dlg.text(), dlg.font_family(), dlg.font_size(), dlg.color()
            )
        self._post_dialog(was_drawing)

    def _on_text_edit_requested(self, idx: int):
        strokes = self._overlay._strokes
        if idx < 0 or idx >= len(strokes):
            return
        stroke = strokes[idx]
        if not stroke or stroke[0][1].get("tool") != "text":
            return
        props = stroke[0][1]
        was_drawing = self._pre_dialog()
        ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
        dlg = TextDialog(
            props.get("color", self._current_color),
            parent=self._overlay if ov_lsw else self,
            initial_text=props.get("text", ""),
            initial_font=props.get("font_family", ""),
            initial_size=int(round(float(props.get("size", 24)))),
        )
        dlg.setWindowTitle("Editar Texto")
        if ov_lsw:
            dlg.setWindowFlags(Qt.WindowType.Popup)
            anchor = stroke[0][0]
            dlg.adjustSize()
            dlg.move(int(anchor.x()) + 8, int(anchor.y()) + 8)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_props = {**props,
                         "text": dlg.text(),
                         "font_family": dlg.font_family(),
                         "size": dlg.font_size(),
                         "color": dlg.color()}
            self._overlay._strokes[idx] = [(stroke[0][0], new_props)]
            self._overlay._canvas = None
            self._overlay._wb_canvas = None
            self._overlay.update()
        self._post_dialog(was_drawing)

    # ── Color ─────────────────────────────────────────────────────────────────

    def _pick_color(self):
        was_drawing = self._pre_dialog()
        ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
        if ov_lsw:
            dlg = QColorDialog(self._current_color, self._overlay)
            dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            dlg.setWindowFlags(Qt.WindowType.Popup)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                color = dlg.currentColor()
                if color.isValid():
                    self._current_color = color
                    self._overlay.set_color(color)
                    self._update_color_button()
        else:
            color = QColorDialog.getColor(self._current_color, self, "Escolher cor")
            if color.isValid():
                self._current_color = color
                self._overlay.set_color(color)
                self._update_color_button()
        self._post_dialog(was_drawing)

    def _update_color_button(self):
        self._color_btn.setIcon(icons.color_dot(self._current_color))

    # ── Screenshot ────────────────────────────────────────────────────────────

    def _do_screenshot(self, clipboard: bool = False):
        import screenshot as sc
        sc.capture(self, tray_icon=self._tray, copy_to_clipboard=clipboard)

    # ── Mode toggles ──────────────────────────────────────────────────────────

    def _toggle_whiteboard(self, checked: bool):
        self._overlay.set_whiteboard(checked)
        self._btn_wb_bg.setVisible(checked)
        self.adjustSize()

    def _pick_whiteboard_bg(self):
        was_drawing = self._pre_dialog()
        ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
        if ov_lsw:
            dlg = QColorDialog(self._wb_bg_color, self._overlay)
            dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            dlg.setWindowFlags(Qt.WindowType.Popup)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                color = dlg.currentColor()
                if color.isValid():
                    self._wb_bg_color = color
                    self._overlay.set_whiteboard_bg(color)
                    self._btn_wb_bg.setIcon(icons.color_dot(color))
        else:
            color = QColorDialog.getColor(self._wb_bg_color, self, "Cor do fundo do quadro")
            if color.isValid():
                self._wb_bg_color = color
                self._overlay.set_whiteboard_bg(color)
                self._btn_wb_bg.setIcon(icons.color_dot(color))
        self._post_dialog(was_drawing)

    def _toggle_spotlight(self, checked: bool):
        self._overlay.set_spotlight(checked)
        self._radius_slider.setVisible(checked)
        self.adjustSize()

    def _toggle_magnifier(self, checked: bool):
        self._magnifier.set_active(checked)
        self._zoom_slider.setVisible(checked)
        self.adjustSize()

    def _toggle_passthrough(self, checked: bool):
        """Botão seta: modo pass-through — desenhos visíveis, input vai para apps."""
        self._passthrough_active = checked
        self._drawing_active = not checked
        if checked:
            # Entra em pass-through: overlay visível, sem input
            self._overlay.set_passthrough(True)
            self._btn_toggle.setIcon(icons.mouse_active())
            for b in self._tool_buttons:
                b.setChecked(False)
        else:
            # Sai do pass-through: retoma desenho (garante overlay visível)
            self._overlay.set_passthrough(False)
            self._overlay.set_active(True)
            self._btn_toggle.setIcon(icons.mouse_pause())
            self._restore_tool_button()
            QTimer.singleShot(100, self._reaffirm_top)

    def _restore_tool_button(self):
        """Remarca o botão da ferramenta activa."""
        btn_map = {
            "pen": self._btn_pen, "highlighter": self._btn_hl,
            "line": self._btn_line, "rect": self._btn_rect,
            "circle": self._btn_circle, "eraser": self._btn_eraser,
            "laser": self._btn_laser, "drag": self._btn_drag, "text": self._btn_text,
        }
        tool = getattr(self._overlay, "_tool", "pen")
        for b in self._tool_buttons:
            b.setChecked(False)
        if tool in btn_map:
            btn_map[tool].setChecked(True)

    def _toggle_drawing(self, checked: bool):
        """Pausa/retoma o overlay completamente (colapso, Tab, botão direito).

        Quando checked=True, o overlay fica OCULTO (desenhos somem).
        Diferente de _toggle_passthrough que mantém desenhos visíveis.
        """
        # Se estava em pass-through, sai dele antes de ocultar
        if self._passthrough_active:
            self._overlay.set_passthrough(False)
            self._passthrough_active = False
            self._btn_toggle.setChecked(checked)

        self._drawing_active = not checked
        self._overlay.set_active(self._drawing_active)
        self._btn_toggle.setIcon(icons.mouse_active() if checked else icons.mouse_pause())
        if checked:
            for b in self._tool_buttons:
                b.setChecked(False)
        else:
            self._restore_tool_button()
            if self._drawing_active:
                QTimer.singleShot(100, self._reaffirm_top)

    # ── Presentation mode ─────────────────────────────────────────────────────

    def _quit_app(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def _toggle_presentation(self, checked: bool):
        self._presentation_mode = checked
        if checked:
            self._edge_timer.start()
            self._hide_timer.start()
            # Sobe para LAYER_OVERLAY para ficar acima de apps fullscreen
            if self._lsw_ptr:
                layershell.set_layer(self._lsw_ptr, layershell.LAYER_OVERLAY)
            _ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
            if _ov_lsw:
                layershell.set_layer(_ov_lsw, layershell.LAYER_OVERLAY)
        else:
            self._edge_timer.stop()
            self._hide_timer.stop()
            self.show()
            self.setWindowOpacity(1.0)
            # Volta a LAYER_TOP quando sai do modo apresentação
            if self._lsw_ptr:
                layershell.set_layer(self._lsw_ptr, layershell.LAYER_TOP)
            _ov_lsw = getattr(self._overlay, '_lsw_ptr', None)
            if _ov_lsw:
                layershell.set_layer(_ov_lsw, layershell.LAYER_TOP)

    def _presentation_auto_hide(self):
        # Colapsado = ícone mínimo já visível; não esconder
        if self._presentation_mode and not self._collapsed:
            self.setWindowOpacity(0.0)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _check_edge_reveal(self):
        if not self._presentation_mode:
            return
        cursor = QCursor.pos()
        # self.pos() retorna (0,0) em superfícies wlr-layer-shell — usa _lsw_pos
        # No modo embed/X11 (sem layer-shell), self.pos() é confiável
        ref = self._lsw_pos if self._lsw_ptr is not None else self.pos()
        tx, ty = ref.x(), ref.y()
        th = max(self.height(), 100)
        near_x = abs(cursor.x() - tx) <= 60
        near_y = ty - 20 <= cursor.y() <= ty + th + 20
        if near_x and near_y:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setWindowOpacity(1.0)
            self.raise_()
            if not self._lsw_ptr:
                import keepabove
                keepabove.set_keepabove()
            self._hide_timer.start()

    def enterEvent(self, event):
        if self._presentation_mode:
            self._hide_timer.stop()
            self.setWindowOpacity(1.0)
        # Wayland: puxa foco de teclado ao entrar na toolbar (não durante drag)
        if not self._dragging:
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

    # ── Tooltip interno ───────────────────────────────────────────────────────

    def _fire_tooltip(self):
        """Exibe o label de tooltip à direita da coluna, expandindo a janela."""
        if not self._tt_widget or not self._tt_widget.toolTip():
            return
        raw = self._tt_widget.toolTip()
        # Remove a dica de tecla no final: "Marcador (H)" → "Marcador"
        text = raw[:raw.rfind(" (")] if " (" in raw else raw
        self._tt_label.setText(text)
        self._tt_label.adjustSize()
        lbl_w = self._tt_label.width()
        lbl_h = self._tt_label.height()
        btn_local = self._tt_widget.mapTo(self, QPoint(0, 0))
        y = btn_local.y() + (self._tt_widget.height() - lbl_h) // 2
        y = max(0, min(y, self.height() - lbl_h))
        self._tt_label.move(_W + 4, y)
        new_w = _W + lbl_w + 8
        # setFixedWidth deixa maximumWidth=_W; precisa remover antes de resize
        self._root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumWidth(new_w)
        self.setMaximumWidth(new_w)
        self.resize(new_w, self.height())
        self._tt_label.show()
        self._tt_label.raise_()

    def _cancel_tooltip(self):
        """Cancela o timer e recolhe o label de tooltip."""
        self._tt_timer.stop()
        self._tt_widget = None
        if self._tt_label.isVisible():
            self._tt_label.hide()
            self._root_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
            self.setFixedWidth(_W)
            self.adjustSize()

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
        # Inicializa _current_screen a partir de _lsw_pos quando a toolbar é mostrada
        if self._current_screen is None:
            from PyQt6.QtWidgets import QApplication
            self._current_screen = (self._screen_at(self._lsw_pos)
                                    or QApplication.primaryScreen())
        # Sem layer-shell (GNOME): sincroniza _lsw_pos com onde o compositor colocou a janela
        if not self._lsw_ptr and self.parent() is None:
            QTimer.singleShot(300, self._sync_lsw_pos_from_widget)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_mask()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_overlay_mask()

    def _sync_overlay_mask(self):
        self._overlay.set_toolbar_region(self.geometry())

    def _sync_lsw_pos_from_widget(self):
        """Atualiza _lsw_pos a partir de self.pos() quando layer-shell não está ativo."""
        p = self.pos()
        if p.x() != 0 or p.y() != 0:
            self._lsw_pos = p

    # ── Multi-monitor ─────────────────────────────────────────────────────────

    def _screen_at(self, pos: QPoint):
        """Retorna o QScreen que contém pos (coords absolutas), ou None."""
        from PyQt6.QtWidgets import QApplication
        for screen in QApplication.screens():
            if screen.geometry().contains(pos):
                return screen
        return None

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        """Clipa pos para que a toolbar não saia da área disponível de nenhum monitor."""
        from PyQt6.QtWidgets import QApplication
        tw = self.width() or _W
        th = self.height() or _BTN * 3
        for scr in QApplication.screens():
            g = scr.availableGeometry()
            if g.contains(pos):
                return QPoint(
                    max(g.left(), min(pos.x(), g.left() + g.width()  - tw)),
                    max(g.top(),  min(pos.y(), g.top()  + g.height() - th)),
                )
        # Fora de todos os monitores — clamp ao ecrã mais próximo
        best, best_d = pos, float('inf')
        for scr in QApplication.screens():
            g = scr.availableGeometry()
            cx = max(g.left(), min(pos.x(), g.left() + g.width()  - tw))
            cy = max(g.top(),  min(pos.y(), g.top()  + g.height() - th))
            cp = QPoint(cx, cy)
            d = (pos.x() - cx) ** 2 + (pos.y() - cy) ** 2
            if d < best_d:
                best_d, best = d, cp
        return best

    def _clamp_to_screen(self, pos: QPoint, screen) -> QPoint:
        """Clipa pos dentro da área disponível de um monitor específico."""
        if screen is None:
            return self._clamp_pos(pos)
        g = screen.availableGeometry()
        tw = self.width() or _W
        th = self.height() or _BTN * 3
        return QPoint(
            max(g.left(), min(pos.x(), g.left() + g.width()  - tw)),
            max(g.top(),  min(pos.y(), g.top()  + g.height() - th)),
        )

    def _change_toolbar_screen(self, screen):
        """Move a superfície layer-shell da toolbar para outro monitor.

        hide+show força a recriação da wl_layer_surface no novo output.
        Overlay é mostrado primeiro; toolbar (último mapeado) fica acima na z-order.
        """
        if not self._lsw_ptr:
            return
        origin = screen.geometry().topLeft()
        rel = self._lsw_pos - origin
        self._overlay.change_screen(screen)
        wh = self.windowHandle()
        if wh:
            wh.setScreen(screen)
        self.hide()
        self.show()
        layershell.move_to(self._lsw_ptr, rel.x(), rel.y())

    # ── Drag + event filter ───────────────────────────────────────────────────

    def _install_event_filters(self):
        """Instala event filter em toda a árvore de widgets filhos do container."""
        self._container.installEventFilter(self)
        for w in self._container.findChildren(QWidget):
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        t = event.type()

        # Botão de saída: ícone vermelho no hover
        if obj is self._btn_exit and not self._dragging:
            if t == QEvent.Type.Enter:
                self._btn_exit.setIcon(icons.exit_btn(hover=True))
            elif t == QEvent.Type.Leave:
                self._btn_exit.setIcon(icons.exit_btn())

        # Tooltip interno com delay de 1.5 s (não depende de popup Qt)
        if isinstance(obj, QPushButton) and not self._dragging:
            if t == QEvent.Type.Enter:
                self._tt_widget = obj
                self._tt_timer.start()
            elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
                self._cancel_tooltip()

        # Suprime hover/enter/leave em filhos durante drag — evita piscar dos botões
        if self._dragging and t in (
            QEvent.Type.HoverMove, QEvent.Type.HoverEnter, QEvent.Type.HoverLeave,
            QEvent.Type.Enter, QEvent.Type.Leave,
        ):
            return True

        # Tab antes do sistema de foco consumir
        if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
            return True

        # Botão direito em qualquer filho → toggle; mas em ferramenta já ativa → modo seta
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            if (isinstance(obj, QPushButton)
                    and obj in self._tool_buttons
                    and obj.isChecked()):
                self._activate_arrow_mode()
                return True
            self._btn_toggle.toggle()
            self._toggle_drawing(self._btn_toggle.isChecked())
            return True

        # Sliders gerenciam o próprio drag — mas durante drag da toolbar têm prioridade zero
        if isinstance(obj, QSlider) and not self._dragging:
            return False

        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            sp = event.scenePosition().toPoint()
            self._drag_start        = sp
            self._drag_start_screen = sp + self._lsw_pos
            self._drag_start_pos    = QPoint(self._lsw_pos)
            self._dragging          = False
            return False  # não consome — deixa o botão ativar normalmente

        if t == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._drag_start is not None and not self._dragging:
                delta = event.scenePosition().toPoint() - self._drag_start
                if abs(delta.x()) + abs(delta.y()) >= 6:
                    self._dragging = True
                    if not self._lsw_ptr and self.parent() is None:
                        # GNOME/sem layer-shell: pede ao compositor para mover a janela.
                        # startSystemMove() envia xdg_toplevel_move; o compositor trata
                        # o resto do arrasto e para de enviar MouseMove para a app.
                        wh = self.windowHandle()
                        if wh:
                            wh.startSystemMove()
                    return True
            if self._dragging and (self.parent() is not None or self._lsw_ptr):
                screen_cursor = event.scenePosition().toPoint() + self._lsw_pos
                cursor_delta  = screen_cursor - self._drag_start_screen
                new_pos = self._clamp_to_screen(self._drag_start_pos + cursor_delta,
                                                self._current_screen)
                if self.parent() is not None:
                    self.move(new_pos)
                else:
                    scr = self._current_screen
                    origin = scr.geometry().topLeft() if scr else QPoint(0, 0)
                    rel = new_pos - origin
                    layershell.move_to(self._lsw_ptr, rel.x(), rel.y())
                    self.update()
                self._lsw_pos = new_pos
                return True
            if self._dragging:
                # startSystemMove() ativo — compositor move; consumir eventos residuais
                return True

        if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_dragging      = self._dragging
            self._drag_start  = None
            self._dragging    = False
            if was_dragging:
                if self._lsw_ptr:
                    cursor_abs = event.scenePosition().toPoint() + self._lsw_pos
                    new_screen = self._screen_at(cursor_abs)
                    if new_screen and new_screen != self._current_screen:
                        self._lsw_pos = self._clamp_pos(cursor_abs)
                        self._current_screen = new_screen
                        self._change_toolbar_screen(new_screen)
                elif self.parent() is None:
                    clamped = self._clamp_pos(self.pos())
                    if clamped != self.pos():
                        self.move(clamped)
                    self._lsw_pos = clamped
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
            Qt.Key.Key_G: "drag",
            Qt.Key.Key_T: "text",
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
