from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QScreen, QPainterPath,
    QRadialGradient, QBrush,
)

LASER_TRAIL_LEN = 18


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

        # background modes (independentes da ferramenta atual)
        self._whiteboard = False
        self._spotlight = False
        self._spotlight_pos: QPoint | None = None
        self._spotlight_radius = 150

        self._setup_window()

        # Reconecta quando monitores são adicionados ou removidos
        app = QApplication.instance()
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)

    def _on_screens_changed(self, _screen=None):
        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        for s in QApplication.screens():
            virtual_geo = virtual_geo.united(s.geometry())
        self.setGeometry(virtual_geo)

    def _setup_window(self):
        # Cobre todos os monitores usando a geometria virtual combinada
        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        for screen in QApplication.screens():
            virtual_geo = virtual_geo.united(screen.geometry())
        self.setGeometry(virtual_geo)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ── Public API ────────────────────────────────────────────────────────

    def set_tool(self, tool: str):
        self._tool = tool
        self._laser_pos = None
        self._laser_trail.clear()
        self._update_tracking()
        cursor = {
            "eraser": Qt.CursorShape.BlankCursor,
            "laser": Qt.CursorShape.BlankCursor,
        }.get(tool, Qt.CursorShape.CrossCursor)
        self.setCursor(cursor)
        self.update()

    def set_color(self, color: QColor):
        self._color = color

    def set_size(self, size: int):
        self._size = size

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            cursor = {
                "eraser": Qt.CursorShape.BlankCursor,
                "laser": Qt.CursorShape.BlankCursor,
            }.get(self._tool, Qt.CursorShape.CrossCursor)
            self.setCursor(cursor)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setCursor(Qt.CursorShape.ArrowCursor)

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

        # 1. Fundo branco (modo quadro branco)
        if self._whiteboard:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 255))

        # 2. Traços salvos e traço atual
        for stroke in self._strokes:
            self._draw_stroke(painter, stroke)
        if self._current_stroke:
            self._draw_stroke(painter, self._current_stroke)

        # 3. Ponteiro laser (acima dos traços)
        if self._tool == "laser" and self._laser_pos:
            self._draw_laser(painter)

        # 4. Spotlight (camada mais alta — escurece tudo exceto o círculo)
        if self._spotlight:
            self._draw_spotlight(painter)

        painter.end()

    def _draw_spotlight(self, painter: QPainter):
        pos = self._spotlight_pos
        r = float(self._spotlight_radius)

        # Usa OddEvenFill: retângulo inteiro – círculo central = área escurecida
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        if pos:
            path.addEllipse(QPointF(pos.x(), pos.y()), r, r)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.fillPath(path, QColor(0, 0, 0, 170))

        # Borda suave no círculo
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
            alpha = int(t * 160)
            radius = max(1, int(t * 6))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 50, 50, alpha)))
            painter.drawEllipse(pt, radius, radius)

        pos = self._laser_pos
        glow_r = 28
        gradient = QRadialGradient(pos.x(), pos.y(), glow_r)
        gradient.setColorAt(0.0,  QColor(255, 255, 255, 220))
        gradient.setColorAt(0.15, QColor(255, 60, 60, 200))
        gradient.setColorAt(0.45, QColor(220, 0, 0, 100))
        gradient.setColorAt(1.0,  QColor(180, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(pos, glow_r, glow_r)
        painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
        painter.drawEllipse(pos, 4, 4)

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
        points = [p for p, _ in stroke]

        if tool in ("pen", "highlighter", "eraser"):
            if len(points) == 1:
                painter.drawPoint(points[0])
            else:
                path = QPainterPath()
                path.moveTo(points[0])
                for pt in points[1:]:
                    path.lineTo(pt)
                painter.drawPath(path)
        elif tool == "line" and len(points) >= 2:
            painter.drawLine(points[0], points[-1])
        elif tool == "rect" and len(points) >= 2:
            painter.drawRect(QRect(points[0], points[-1]).normalized())
        elif tool == "circle" and len(points) >= 2:
            painter.drawEllipse(QRect(points[0], points[-1]).normalized())

    def _brush_props(self) -> dict:
        return {"tool": self._tool, "color": QColor(self._color), "size": self._size}

    def _update_tracking(self):
        needs = (self._tool == "laser") or self._spotlight
        self.setMouseTracking(needs)
