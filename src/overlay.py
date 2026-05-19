import os
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QScreen, QPainterPath,
    QRadialGradient, QBrush,
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

        # laser
        self._laser_pos: QPoint | None = None
        self._laser_trail: list[QPoint] = []

        # modos de fundo
        self._whiteboard = False
        self._spotlight = False
        self._spotlight_pos: QPoint | None = None
        self._spotlight_radius = 150

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

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.show()
            self._refresh_cursor()
        else:
            # hide() é a única abordagem garantida no Wayland: flags como
            # WindowTransparentForInput dependem do compositor implementar
            # wl_surface.set_input_region, o que nem todos fazem corretamente.
            self.hide()

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

    def undo(self):
        if self._strokes:
            self._undo_stack.append(self._strokes.pop())
            self.update()

    def redo(self):
        if self._undo_stack:
            self._strokes.append(self._undo_stack.pop())
            self.update()

    def clear(self):
        self._strokes.clear()
        self._undo_stack.clear()
        self.update()

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._tool == "laser":
            return
        self._drawing = True
        self._current_stroke = [(event.pos(), self._brush_props())]
        self._undo_stack.clear()

    def mouseMoveEvent(self, event):
        pos = event.pos()

        if self._spotlight:
            self._spotlight_pos = pos
            self.update()

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
        self.update()

    def mouseReleaseEvent(self, event):
        if self._tool == "laser" or not self._drawing:
            return
        self._drawing = False
        if self._current_stroke:
            self._strokes.append(list(self._current_stroke))
        self._current_stroke = []
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._whiteboard:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 255))

        for stroke in self._strokes:
            self._draw_stroke(painter, stroke)
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
