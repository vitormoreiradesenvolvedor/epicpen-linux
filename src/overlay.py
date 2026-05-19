from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect
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

        # laser state
        self._laser_pos: QPoint | None = None
        self._laser_trail: list[QPoint] = []

        self._setup_window()

    def _setup_window(self):
        screen: QScreen = QApplication.primaryScreen()
        self.setGeometry(screen.geometry())
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
        if tool == "eraser":
            self.setCursor(Qt.CursorShape.BlankCursor)
        elif tool == "laser":
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.setMouseTracking(True)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setMouseTracking(False)
            self._laser_pos = None
            self._laser_trail.clear()
            self.update()

    def set_color(self, color: QColor):
        self._color = color

    def set_size(self, size: int):
        self._size = size

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            tool_cursor = {
                "eraser": Qt.CursorShape.BlankCursor,
                "laser": Qt.CursorShape.BlankCursor,
            }
            self.setCursor(tool_cursor.get(self._tool, Qt.CursorShape.CrossCursor))
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setCursor(Qt.CursorShape.ArrowCursor)

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
        if self._tool == "laser":
            pos = event.pos()
            self._laser_trail.append(pos)
            if len(self._laser_trail) > LASER_TRAIL_LEN:
                self._laser_trail.pop(0)
            self._laser_pos = pos
            self.update()
            return
        if not self._drawing:
            return
        self._current_stroke.append((event.pos(), self._brush_props()))
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

        for stroke in self._strokes:
            self._draw_stroke(painter, stroke)

        if self._current_stroke:
            self._draw_stroke(painter, self._current_stroke)

        if self._tool == "laser" and self._laser_pos:
            self._draw_laser(painter)

        painter.end()

    def _draw_laser(self, painter: QPainter):
        trail = self._laser_trail
        count = len(trail)

        # Trail: pontos encolhendo e perdendo opacidade
        for i, pt in enumerate(trail[:-1]):
            t = i / max(count - 1, 1)
            alpha = int(t * 160)
            radius = max(1, int(t * 6))
            c = QColor(255, 50, 50, alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(pt, radius, radius)

        # Ponto principal com glow radial
        pos = self._laser_pos
        glow_r = 28
        gradient = QRadialGradient(pos.x(), pos.y(), glow_r)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 220))
        gradient.setColorAt(0.15, QColor(255, 60, 60, 200))
        gradient.setColorAt(0.45, QColor(220, 0, 0, 100))
        gradient.setColorAt(1.0, QColor(180, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(pos, glow_r, glow_r)

        # Núcleo sólido brilhante
        painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
        painter.drawEllipse(pos, 4, 4)

    def _draw_stroke(self, painter: QPainter, stroke: list):
        if not stroke:
            return

        props = stroke[0][1]
        tool = props["tool"]
        color = props["color"]
        size = props["size"]

        if tool == "eraser":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            pen = QPen(Qt.GlobalColor.transparent, size * 4, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        elif tool == "highlighter":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            hi_color = QColor(color)
            hi_color.setAlpha(80)
            pen = QPen(hi_color, size * 6, Qt.PenStyle.SolidLine,
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
