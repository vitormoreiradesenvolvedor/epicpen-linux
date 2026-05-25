import os
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QScreen, QPainterPath,
    QRadialGradient, QBrush, QPixmap,
)
from cursors import make_pen_cursor, make_eraser_cursor, make_crosshair_cursor

LASER_TRAIL_LEN = 18

# True quando rodando no backend Wayland nativo (não XWayland)
IS_WAYLAND = (
    os.environ.get("WAYLAND_DISPLAY") is not None
    and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"
)


class OverlayWindow(QWidget):
    """Janela transparente que cobre toda a tela para desenho."""

    def __init__(self):
        super().__init__()
        self._strokes: list[list[tuple[QPoint, dict]]] = []
        self._current_stroke: list[tuple[QPoint, dict]] = []
        self._undo_stack: list[list[tuple[QPoint, dict]]] = []

        self._tool = "pen"
        self._color = QColor("#FF0000")
        self._size = 3
        self._drawing = False
        self._active = True

        # drag tool state
        self._drag_stroke_idx: int | None = None
        self._drag_last_pos: QPoint | None = None

        # laser
        self._laser_pos: QPoint | None = None
        self._laser_trail: list[QPoint] = []

        # modos de fundo
        self._whiteboard = False
        self._spotlight = False
        self._spotlight_pos: QPoint | None = None
        self._spotlight_radius = 150

        # Canvas acumulado: strokes concluídos são commitados aqui uma vez.
        # paintEvent faz blit deste pixmap + desenha apenas o stroke actual.
        # Invalida (None) quando o widget é redimensionado ou undo/clear ocorre.
        self._canvas: QPixmap | None = None

        # Scratch canvas para a borracha activa: cópia do canvas principal onde
        # cada novo segmento apagado é aplicado incrementalmente durante o drag.
        # paintEvent faz blit deste em vez de canvas+stroke enquanto está activo.
        self._erase_scratch: QPixmap | None = None

        # rect global da toolbar — usado no modo dois-janelas (legado)
        self._toolbar_global_rect = None
        # widget da toolbar embutida — usado no modo janela-única
        self._toolbar_widget = None
        # True quando layer-shell está ativo: show/hide usam máscara em vez de
        # mapear/desmapar a superfície (evita perder a configuração layer-shell)
        self._layer_shell_active = False
        # Chamado após remapar a superfície (layer-shell); usado em main.py
        # para forçar o toolbar de volta ao topo da z-order.
        self._on_remapped = None

        self._setup_window()
        self._refresh_cursor()

    def _setup_window(self):
        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        for screen in QApplication.screens():
            virtual_geo = virtual_geo.united(screen.geometry())
        self.setGeometry(virtual_geo)

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus  # não rouba foco de outras apps
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        app = QApplication.instance()
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)

    def _on_screens_changed(self, _screen=None):
        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        for s in QApplication.screens():
            virtual_geo = virtual_geo.united(s.geometry())
        self.setGeometry(virtual_geo)
        self._apply_input_mask()

    # ── Cursores ──────────────────────────────────────────────────────────

    def _refresh_cursor(self):
        """Atualiza o cursor de acordo com a ferramenta e estado atuais."""
        if not self._active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._tool == "laser":
            self.setCursor(Qt.CursorShape.BlankCursor)
        elif self._tool == "eraser":
            self.setCursor(make_eraser_cursor(self._size))
        elif self._tool in ("line", "rect", "circle"):
            self.setCursor(make_crosshair_cursor())
        elif self._tool == "drag":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:  # pen, highlighter
            self.setCursor(make_pen_cursor(self._color))

    # ── Public API ────────────────────────────────────────────────────────

    def set_tool(self, tool: str):
        self._tool = tool
        self._laser_pos = None
        self._laser_trail.clear()
        self._update_tracking()
        self._refresh_cursor()
        self.update()

    def set_color(self, color: QColor):
        self._color = color
        if self._tool in ("pen", "highlighter"):
            self.setCursor(make_pen_cursor(color))

    def set_size(self, size: int):
        self._size = size
        if self._tool == "eraser":
            self.setCursor(make_eraser_cursor(size))

    def embed_toolbar(self, toolbar):
        """
        Embeds toolbar as a child widget of this overlay (single-window mode).
        Call BEFORE show(). The toolbar's saved position is preserved via its _cfg.
        """
        pos = toolbar._cfg.get("toolbar_pos", {"x": 20, "y": 150})
        toolbar.setParent(self)
        # WA_TranslucentBackground num filho de janela transparente torna o filho invisível;
        # sem ele, as áreas não pintadas caem no fundo transparente do pai (correto).
        toolbar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # setParent() resets widget position to (0,0) — restore from config
        toolbar.move(pos.get("x", 20), pos.get("y", 150))
        toolbar.show()
        self._toolbar_widget = toolbar
        self._apply_input_mask()

    def set_toolbar_region(self, local_rect):
        """Legado: chamado pelo toolbar para sincronizar máscara (modo dois-janelas)."""
        self._toolbar_global_rect = local_rect
        self._apply_input_mask()

    def _apply_input_mask(self):
        from PyQt6.QtGui import QRegion

        if self._toolbar_widget is not None:
            # Embed: só a área da toolbar recebe input quando desenho inativo
            if self._active:
                self.clearMask()
            else:
                tb_rect = self._toolbar_widget.geometry()
                self.setMask(QRegion(tb_rect) if not tb_rect.isEmpty()
                             else self._offscreen_region())
            return

        if self._layer_shell_active:
            # Layer-shell duas janelas: o toolbar é superfície separada acima na z-order.
            # O compositor entrega cliques ao toolbar sem precisar de máscara no overlay.
            # widget.geometry() é lixo no Wayland — qualquer subtração seria errada.
            if self._active:
                self.clearMask()
            # inactive: set_active() já chamou super().hide() — nada a fazer aqui
            return

        # Modo legado dois-janelas (X11 / sem layer-shell)
        if not self._active:
            self.setMask(self._offscreen_region())
            return

        # Área base: tela toda
        full = QRegion(self.rect())
        origin = self.geometry().topLeft()

        # Subtrai área da toolbar (modo legado dois-janelas)
        if self._toolbar_global_rect is not None:
            local = self._toolbar_global_rect.translated(-origin)
            full = full.subtracted(QRegion(local))

        # Subtrai área de painel de cada tela (availableGeometry ≠ geometry)
        # para que o painel KDE sempre receba eventos de input.
        for screen in QApplication.screens():
            panel = QRegion(screen.geometry().translated(-origin)).subtracted(
                        QRegion(screen.availableGeometry().translated(-origin)))
            if not panel.isEmpty():
                full = full.subtracted(panel)

        self.setMask(full)

    def set_active(self, active: bool):
        self._active = active
        if self._toolbar_widget is not None:
            # Modo embed: nunca esconde — usa máscara para pass-through
            self._apply_input_mask()
            self._refresh_cursor()
            self.update()
        elif self._layer_shell_active:
            # Layer-shell: unmap real libera o input para outras janelas
            if active:
                was_hidden = not self.isVisible()
                if was_hidden:
                    super().show()
                self.clearMask()
                self._refresh_cursor()
                self.update()
                # Superfície remapeada vai para o topo da z-order dentro de Layer::Top,
                # ficando acima do toolbar. Notifica main.py para recolocar o toolbar.
                if was_hidden and self._on_remapped:
                    self._on_remapped()
            else:
                super().hide()
        else:
            # Fallback legacy (X11, sem layer-shell): show/hide real
            if active:
                super().show()
                self.raise_()
                self._apply_input_mask()
                self._refresh_cursor()
            else:
                super().hide()

    def show(self):
        if self._layer_shell_active:
            self._active = True
            self.clearMask()
            self._refresh_cursor()
            self.update()
            if not self.isVisible():
                super().show()  # primeira vez: cria superfície layer-shell
        else:
            super().show()

    def hide(self):
        self._active = False
        super().hide()

    def _offscreen_region(self):
        """1×1 px fora do ecrã: wl_surface recebe set_input_region(empty_region)
        em vez de set_input_region(null) que o Qt usa para QRegion() vazio."""
        from PyQt6.QtGui import QRegion
        return QRegion(max(self.width(), 9999) + 1, 0, 1, 1)

    def change_screen(self, screen):
        """Move o overlay para outro monitor (layer-shell).

        hide+show força a recriação da wl_layer_surface no novo output.
        Não dispara _on_remapped (que é reservado para toggle de modo de desenho).
        """
        if not self._layer_shell_active:
            return
        qwindow = self.windowHandle()
        if qwindow is None:
            return
        was_visible = self.isVisible()
        if was_visible:
            super().hide()
        qwindow.setScreen(screen)
        if was_visible:
            super().show()
            if self._active:
                self.clearMask()
                self._refresh_cursor()
                self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._layer_shell_active:
            self._apply_input_mask()

    def set_whiteboard(self, active: bool):
        self._whiteboard = active
        self.update()

    def set_spotlight(self, active: bool):
        self._spotlight = active
        self._update_tracking()
        if not active:
            self._spotlight_pos = None
        self.update()

    def set_spotlight_radius(self, radius: int):
        self._spotlight_radius = radius
        self.update()

    # ── Canvas acumulado ──────────────────────────────────────────────────

    def _ensure_canvas(self):
        if self._canvas is None or self._canvas.size() != self.size():
            self._canvas = QPixmap(self.size())
            self._canvas.fill(Qt.GlobalColor.transparent)
            self._rebuild_canvas()

    def _rebuild_canvas(self):
        """Redesenha todos os strokes concluídos no canvas (usado por undo/resize)."""
        if self._canvas is None:
            return
        self._canvas.fill(Qt.GlobalColor.transparent)
        if not self._strokes:
            return
        p = QPainter(self._canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for stroke in self._strokes:
            self._draw_stroke(p, stroke)
        p.end()

    def _commit_stroke(self, stroke: list) -> None:
        """Pinta um stroke directamente no canvas sem redesenhar tudo."""
        if self._canvas is None:
            return
        p = QPainter(self._canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_stroke(p, stroke)
        p.end()

    # ── Undo / redo / clear ───────────────────────────────────────────────

    def undo(self):
        if self._strokes:
            self._undo_stack.append(self._strokes.pop())
            self._erase_scratch = None
            self._canvas = None   # força rebuild no próximo paint
            self.update()

    def redo(self):
        if self._undo_stack:
            stroke = self._undo_stack.pop()
            self._strokes.append(stroke)
            self._ensure_canvas()
            self._commit_stroke(stroke)
            self.update()

    def clear(self):
        self._strokes.clear()
        self._undo_stack.clear()
        self._erase_scratch = None
        if self._canvas is not None:
            self._canvas.fill(Qt.GlobalColor.transparent)
        self.update()

    # ── Drag tool helpers ─────────────────────────────────────────────────────

    _DRAG_THRESHOLD = 20  # pixels

    def _find_stroke_at(self, pos: QPoint) -> "int | None":
        best_idx, best_dist = None, float("inf")
        for i, stroke in enumerate(self._strokes):
            for pt, _ in stroke:
                d = abs(pt.x() - pos.x()) + abs(pt.y() - pos.y())
                if d < best_dist:
                    best_dist, best_idx = d, i
        return best_idx if best_idx is not None and best_dist <= self._DRAG_THRESHOLD else None

    def _translate_stroke(self, idx: int, delta: QPoint):
        self._strokes[idx] = [
            (QPoint(pt.x() + delta.x(), pt.y() + delta.y()), props)
            for pt, props in self._strokes[idx]
        ]

    def _scale_stroke(self, idx: int, factor: float):
        stroke = self._strokes[idx]
        if not stroke:
            return
        if stroke[0][1].get("tool") == "text":
            pt, props = stroke[0]
            self._strokes[idx] = [(pt, {**props, "size": max(6, int(props["size"] * factor))})]
        else:
            pts = [pt for pt, _ in stroke]
            cx = sum(p.x() for p in pts) / len(pts)
            cy = sum(p.y() for p in pts) / len(pts)
            self._strokes[idx] = [
                (QPoint(int(cx + (pt.x() - cx) * factor),
                        int(cy + (pt.y() - cy) * factor)), props)
                for pt, props in stroke
            ]
        self._canvas = None
        self.update()

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._tool == "laser":
            return
        if self._tool == "drag":
            idx = self._find_stroke_at(event.pos())
            if idx is not None:
                self._drag_stroke_idx = idx
                self._drag_last_pos = event.pos()
                self._drawing = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self._drawing = True
        self._current_stroke = [(event.pos(), self._brush_props())]
        self._undo_stack.clear()
        # Borracha: inicia scratch canvas como cópia do estado actual
        if self._tool == "eraser":
            self._ensure_canvas()
            self._erase_scratch = QPixmap(self._canvas)
            p = QPainter(self._erase_scratch)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_stroke(p, self._current_stroke)
            p.end()

    def mouseMoveEvent(self, event):
        pos = event.pos()

        if self._spotlight:
            self._spotlight_pos = pos
            self.update()

        if self._tool == "drag" and self._drawing and self._drag_stroke_idx is not None:
            delta = QPoint(pos.x() - self._drag_last_pos.x(),
                           pos.y() - self._drag_last_pos.y())
            self._translate_stroke(self._drag_stroke_idx, delta)
            self._drag_last_pos = pos
            self._canvas = None
            self.update()
            return

        if self._tool == "laser":
            self._laser_trail.append(pos)
            if len(self._laser_trail) > LASER_TRAIL_LEN:
                self._laser_trail.pop(0)
            self._laser_pos = pos
            self.update()
            return

        if not self._drawing:
            return
        self._current_stroke.append((pos, self._brush_props()))

        # Borracha: aplica apenas o novo segmento no scratch — O(1) por frame
        if self._tool == "eraser" and self._erase_scratch is not None:
            pts = self._current_stroke
            if len(pts) >= 2:
                p = QPainter(self._erase_scratch)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_stroke(p, pts[-2:])
                p.end()

        self.update()

    def mouseReleaseEvent(self, event):
        if self._tool == "drag":
            if self._drawing:
                self._drawing = False
                self._drag_stroke_idx = None
                self._drag_last_pos = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self._tool == "laser" or not self._drawing:
            return
        self._drawing = False
        if self._current_stroke:
            stroke = list(self._current_stroke)
            self._strokes.append(stroke)
            if self._erase_scratch is not None:
                # Borracha: scratch já tem o resultado final — promove a canvas
                self._canvas = self._erase_scratch
                self._erase_scratch = None
            else:
                self._ensure_canvas()
                self._commit_stroke(stroke)
        self._current_stroke = []
        self.update()

    def wheelEvent(self, event):
        if self._active and self._tool == "drag":
            idx = self._find_stroke_at(event.position().toPoint())
            if idx is not None:
                factor = 1.1 if event.angleDelta().y() > 0 else 0.9
                self._scale_stroke(idx, factor)
            event.accept()
        else:
            event.ignore()

    # ── Painting ──────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._erase_scratch = None
        self._canvas = None   # tamanho mudou — recria no próximo paint

    def paintEvent(self, _event):
        self._ensure_canvas()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._whiteboard:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 255))

        # Borracha activa: blit do scratch (já tem o apagado acumulado) — O(1)
        # Outros casos: blit do canvas + stroke em progresso
        if self._erase_scratch is not None:
            painter.drawPixmap(0, 0, self._erase_scratch)
        else:
            painter.drawPixmap(0, 0, self._canvas)
            if self._current_stroke:
                self._draw_stroke(painter, self._current_stroke)

        if self._tool == "laser" and self._laser_pos:
            self._draw_laser(painter)

        if self._spotlight:
            self._draw_spotlight(painter)

        painter.end()

    def _draw_stroke(self, painter: QPainter, stroke: list):
        if not stroke:
            return

        props = stroke[0][1]
        tool, color, size = props["tool"], props["color"], props["size"]

        if tool == "eraser":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            pen = QPen(Qt.GlobalColor.transparent, size * 4, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        elif tool == "highlighter":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            hi = QColor(color)
            hi.setAlpha(80)
            pen = QPen(hi, size * 6, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(color, size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # QPainterPath exige QPointF — converte aqui
        raw   = [p for p, _ in stroke]
        pts_f = [QPointF(p) for p in raw]

        if tool in ("pen", "highlighter", "eraser"):
            if len(pts_f) == 1:
                painter.drawPoint(pts_f[0])
            else:
                path = QPainterPath()
                path.moveTo(pts_f[0])
                for pt in pts_f[1:]:
                    path.lineTo(pt)
                painter.drawPath(path)
        elif tool == "line" and len(raw) >= 2:
            painter.drawLine(pts_f[0], pts_f[-1])
        elif tool == "rect" and len(raw) >= 2:
            painter.drawRect(QRect(raw[0], raw[-1]).normalized())
        elif tool == "circle" and len(raw) >= 2:
            painter.drawEllipse(QRect(raw[0], raw[-1]).normalized())

    def _draw_spotlight(self, painter: QPainter):
        pos = self._spotlight_pos
        r = float(self._spotlight_radius)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        if pos:
            path.addEllipse(QPointF(pos.x(), pos.y()), r, r)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.fillPath(path, QColor(0, 0, 0, 170))

        if pos:
            gradient = QRadialGradient(QPointF(pos.x(), pos.y()), r + 30)
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(0.7, QColor(0, 0, 0, 0))
            gradient.setColorAt(1.0, QColor(0, 0, 0, 100))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawEllipse(QPointF(pos.x(), pos.y()), r + 30, r + 30)

    def _draw_laser(self, painter: QPainter):
        trail = self._laser_trail
        count = len(trail)
        for i, pt in enumerate(trail[:-1]):
            t = i / max(count - 1, 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 50, 50, int(t * 160))))
            painter.drawEllipse(QPointF(pt), max(1.0, t * 6), max(1.0, t * 6))

        pos = self._laser_pos
        glow_r = 28.0
        gradient = QRadialGradient(pos.x(), pos.y(), glow_r)
        gradient.setColorAt(0.0,  QColor(255, 255, 255, 220))
        gradient.setColorAt(0.15, QColor(255, 60, 60, 200))
        gradient.setColorAt(0.45, QColor(220, 0, 0, 100))
        gradient.setColorAt(1.0,  QColor(180, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(pos), glow_r, glow_r)
        painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
        painter.drawEllipse(QPointF(pos), 4.0, 4.0)

    def _brush_props(self) -> dict:
        return {"tool": self._tool, "color": QColor(self._color), "size": self._size}

    def _update_tracking(self):
        self.setMouseTracking((self._tool == "laser") or self._spotlight)
