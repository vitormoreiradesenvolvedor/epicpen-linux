from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QBrush, QCursor

DIAMETER = 220
OFFSET_Y = 30    # distância vertical abaixo do cursor


class MagnifierWindow(QWidget):
    """Lupa circular flutuante que amplifica a região ao redor do cursor."""

    def __init__(self):
        super().__init__()
        self._zoom = 3
        self._cursor_pos = QPoint(0, 0)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(DIAMETER, DIAMETER)

        self._timer = QTimer(self)
        self._timer.setInterval(16)   # ~60 fps
        self._timer.timeout.connect(self._tick)

        self.hide()

    # ── Public API ────────────────────────────────────────────────────────

    def set_zoom(self, zoom: int):
        self._zoom = max(2, min(zoom, 6))

    def set_active(self, active: bool):
        if active:
            self._timer.start()
            self.show()
        else:
            self._timer.stop()
            self.hide()

    # ── Internal ──────────────────────────────────────────────────────────

    def _tick(self):
        pos = QCursor.pos()
        if pos != self._cursor_pos:
            self._cursor_pos = pos
            self._reposition(pos)
            self.update()

    def _reposition(self, cursor: QPoint):
        screen = QApplication.primaryScreen().geometry()
        x = cursor.x() - DIAMETER // 2
        y = cursor.y() + OFFSET_Y

        # Evita sair da tela
        x = max(screen.left(), min(x, screen.right() - DIAMETER))
        y = max(screen.top(), min(y, screen.bottom() - DIAMETER))

        # Se a lupa iria cobrir o cursor, põe acima
        if y + DIAMETER > screen.bottom():
            y = cursor.y() - DIAMETER - OFFSET_Y

        self.move(x, y)

    def paintEvent(self, _event):
        screen = QApplication.primaryScreen()
        cap_size = DIAMETER // self._zoom
        cx, cy = self._cursor_pos.x(), self._cursor_pos.y()

        capture_rect = QRect(
            cx - cap_size // 2,
            cy - cap_size // 2,
            cap_size,
            cap_size,
        )
        raw = screen.grabWindow(
            0,
            capture_rect.x(), capture_rect.y(),
            capture_rect.width(), capture_rect.height(),
        )
        scaled = raw.scaled(
            DIAMETER, DIAMETER,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Clip circular
        clip = QPainterPath()
        clip.addEllipse(0, 0, DIAMETER, DIAMETER)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, scaled)
        painter.setClipping(False)

        # Borda branca com sombra interna sutil
        painter.setPen(QPen(QColor(255, 255, 255, 220), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(2, 2, DIAMETER - 4, DIAMETER - 4)

        # Anel escuro externo
        painter.setPen(QPen(QColor(0, 0, 0, 100), 1))
        painter.drawEllipse(0, 0, DIAMETER, DIAMETER)

        # Mira central
        mid = DIAMETER // 2
        painter.setPen(QPen(QColor(255, 50, 50, 200), 1))
        painter.drawLine(mid - 12, mid, mid - 4, mid)
        painter.drawLine(mid + 4, mid, mid + 12, mid)
        painter.drawLine(mid, mid - 12, mid, mid - 4)
        painter.drawLine(mid, mid + 4, mid, mid + 12)

        # Ponto central
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 50, 50, 200)))
        painter.drawEllipse(mid - 2, mid - 2, 4, 4)

        painter.end()
