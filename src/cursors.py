from PyQt6.QtGui import QCursor, QPixmap, QPainter, QColor, QPen, QPainterPath, QBrush
from PyQt6.QtCore import Qt


def make_pen_cursor(color: QColor | None = None) -> QCursor:
    """Cursor diagonal em forma de caneta com ponta colorida. Hot-spot na ponta."""
    SIZE = 32
    px = QPixmap(SIZE, SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    tip_color = color or QColor(220, 50, 50)

    # Corpo branco com borda preta
    body = QPainterPath()
    body.moveTo(5, 27)
    body.lineTo(7, 30)
    body.lineTo(27, 5)
    body.lineTo(24, 3)
    body.closeSubpath()
    p.setPen(QPen(QColor(0, 0, 0, 220), 1))
    p.setBrush(QBrush(QColor(245, 245, 245)))
    p.drawPath(body)

    # Faixa metálica central
    band = QPainterPath()
    band.moveTo(13, 19)
    band.lineTo(16, 16)
    band.lineTo(20, 20)
    band.lineTo(17, 23)
    band.closeSubpath()
    p.setBrush(QBrush(QColor(180, 180, 200)))
    p.drawPath(band)

    # Ponta colorida
    tip = QPainterPath()
    tip.moveTo(5, 27)
    tip.lineTo(7, 30)
    tip.lineTo(2, 31)
    tip.closeSubpath()
    p.setBrush(QBrush(tip_color))
    p.drawPath(tip)

    # Pixel brilhante na ponta
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, 200)))
    p.drawEllipse(4, 29, 2, 2)

    p.end()
    return QCursor(px, 2, 30)   # hot-spot = ponta da caneta


def make_eraser_cursor(brush_size: int = 3) -> QCursor:
    """Cursor quadrado semi-transparente para a borracha, proporcional ao tamanho."""
    side = max(12, min(brush_size * 4, 64))
    px = QPixmap(side, side)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(60, 60, 60, 200), 1.5))
    p.setBrush(QBrush(QColor(255, 255, 255, 100)))
    p.drawRoundedRect(1, 1, side - 2, side - 2, 3, 3)
    p.end()
    return QCursor(px, side // 2, side // 2)   # hot-spot = centro


def make_arrow_cursor(color: QColor | None = None) -> QCursor:
    """Seta de mouse de alta visibilidade para ferramentas de forma.

    Seta clássica branca com contorno preto espesso e sombra — visível
    sobre qualquer fundo (a mira fina sumia em telas claras/escuras).
    Um ponto colorido junto à base indica a cor da forma ativa.
    """
    SIZE = 30
    px = QPixmap(SIZE, SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    def _arrow_path(dx: float, dy: float) -> QPainterPath:
        a = QPainterPath()
        a.moveTo(1 + dx, 1 + dy)
        a.lineTo(1 + dx, 21 + dy)
        a.lineTo(6 + dx, 16 + dy)
        a.lineTo(10 + dx, 25 + dy)
        a.lineTo(14 + dx, 23 + dy)
        a.lineTo(10 + dx, 14.5 + dy)
        a.lineTo(17 + dx, 14.5 + dy)
        a.closeSubpath()
        return a

    # Sombra deslocada — descola a seta do fundo
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(0, 0, 0, 90)))
    p.drawPath(_arrow_path(2.0, 2.0))

    # Corpo branco com contorno preto espesso
    p.setPen(QPen(QColor(0, 0, 0, 235), 2.0))
    p.setBrush(QBrush(QColor(255, 255, 255)))
    p.drawPath(_arrow_path(0.0, 0.0))

    # Ponto com a cor da ferramenta
    if color is not None:
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.setBrush(QBrush(color))
        p.drawEllipse(19, 19, 9, 9)

    p.end()
    return QCursor(px, 1, 1)   # hot-spot = ponta da seta


def make_crosshair_cursor() -> QCursor:
    """Mira fina para ferramentas de forma (linha, rect, elipse)."""
    SIZE = 24
    px = QPixmap(SIZE, SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    mid = SIZE // 2
    p.setPen(QPen(QColor(0, 0, 0, 200), 1))
    p.drawLine(mid, 0, mid, mid - 3)
    p.drawLine(mid, mid + 3, mid, SIZE)
    p.drawLine(0, mid, mid - 3, mid)
    p.drawLine(mid + 3, mid, SIZE, mid)
    p.setPen(QPen(QColor(255, 255, 255, 180), 1))
    p.drawLine(mid, 1, mid, mid - 4)
    p.drawLine(mid, mid + 4, mid, SIZE - 1)
    p.drawLine(1, mid, mid - 4, mid)
    p.drawLine(mid + 4, mid, SIZE - 1, mid)
    p.end()
    return QCursor(px, mid, mid)
