import os
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QBrush, QCursor, QPixmap, QFont

from screenshot import grab_region, _IS_WAYLAND

DIAMETER = 220
OFFSET_Y = 30


class MagnifierWindow(QWidget):
    """Lupa circular flutuante que amplifica a região ao redor do cursor."""

    def __init__(self):
        super().__init__()
        self._zoom   = 3
        self._cursor_pos = QPoint(0, 0)
        self._last_px: QPixmap | None = None
        self._unavailable = False   # True se nenhum método de captura funcionar

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(DIAMETER, DIAMETER)

        # Wayland: grim tem latência maior, reduz para ~15fps para não travar
        interval = 66 if _IS_WAYLAND else 16
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
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

    def _screen_at(self, pos: QPoint):
        for s in QApplication.screens():
            if s.geometry().contains(pos):
                return s
        return QApplication.primaryScreen()

    def _tick(self):
        pos = QCursor.pos()
        if pos == self._cursor_pos:
            return
        self._cursor_pos = pos
        self._reposition(pos)

        cap_size = DIAMETER // self._zoom
        cx, cy = pos.x(), pos.y()
        x = cx - cap_size // 2
        y = cy - cap_size // 2

        px = grab_region(x, y, cap_size, cap_size)
        if px is None:
            if not self._unavailable:
                self._unavailable = True
                self.update()
        else:
            self._unavailable = False
            self._last_px = px.scaled(
                DIAMETER, DIAMETER,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.update()

    def _reposition(self, cursor: QPoint):
        screen = self._screen_at(cursor).geometry()
        x = cursor.x() - DIAMETER // 2
        y = cursor.y() + OFFSET_Y

        x = max(screen.left(),  min(x, screen.right()  - DIAMETER))
        y = max(screen.top(),   min(y, screen.bottom() - DIAMETER))

        if y + DIAMETER > screen.bottom():
            y = cursor.y() - DIAMETER - OFFSET_Y

        self.move(x, y)

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        clip = QPainterPath()
        clip.addEllipse(0, 0, DIAMETER, DIAMETER)
        painter.setClipPath(clip)

        if self._unavailable or self._last_px is None:
            self._draw_unavailable(painter)
        else:
            painter.drawPixmap(0, 0, self._last_px)

        painter.setClipping(False)

        # Borda branca
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
        painter.drawLine(mid + 4,  mid, mid + 12, mid)
        painter.drawLine(mid, mid - 12, mid, mid - 4)
        painter.drawLine(mid, mid + 4,  mid, mid + 12)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 50, 50, 200)))
        painter.drawEllipse(mid - 2, mid - 2, 4, 4)

        painter.end()

    def _draw_unavailable(self, painter: QPainter):
        """Mostra mensagem quando captura não está disponível no Wayland."""
        painter.fillRect(0, 0, DIAMETER, DIAMETER, QColor(30, 30, 40, 220))
        painter.setPen(QColor(255, 255, 255, 200))
        font = QFont("Sans Serif", 9)
        painter.setFont(font)
        painter.drawText(
            QRect(10, DIAMETER // 2 - 30, DIAMETER - 20, 60),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            "Lupa indisponível\nInstale o 'grim'\npara Wayland",
        )
