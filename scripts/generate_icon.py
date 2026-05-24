#!/usr/bin/env python3
"""
Gera resources/icons/epicpen.png (256x256) usando PyQt6.
Executado durante o build do AppImage.
"""
import sys
from pathlib import Path

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import (
        QPixmap, QPainter, QColor, QBrush, QPen,
        QPainterPath, QLinearGradient,
    )
    from PyQt6.QtCore import Qt, QPointF, QRectF
except ImportError:
    print("PyQt6 não encontrado — ícone não gerado.", file=sys.stderr)
    sys.exit(0)

SIZE = 256
OUT  = Path(__file__).parent.parent / "resources" / "icons" / "epicpen.png"

# Escala relativamente ao viewBox SVG original (28x28)
S = SIZE / 28


def draw_icon() -> QPixmap:
    px = QPixmap(SIZE, SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # ── Fundo: rounded rect com gradiente linear (#4f8eff → #7c5fff) ────────
    grad = QLinearGradient(QPointF(0, 0), QPointF(SIZE, SIZE))
    grad.setColorAt(0.0, QColor(0x4f, 0x8e, 0xff))
    grad.setColorAt(1.0, QColor(0x7c, 0x5f, 0xff))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    radius = 7 * S  # rx=7 no SVG original
    p.drawRoundedRect(QRectF(0, 0, SIZE, SIZE), radius, radius)

    # ── Forma "A" / chevron (caneta a anotar) ───────────────────────────────
    # SVG: M8 20 L14 8 L20 20  (stroke-width=2.2, round caps/joins)
    pen = QPen(QColor(255, 255, 255), 2.2 * S,
               Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(QPointF(8 * S, 20 * S))
    path.lineTo(QPointF(14 * S, 8 * S))
    path.lineTo(QPointF(20 * S, 20 * S))
    p.drawPath(path)

    # ── Ponto branco (ponta da caneta / laser) ───────────────────────────────
    # SVG: circle cx=19 cy=10 r=2.5 fill=#fff opacity=.9
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, int(0.9 * 255))))
    p.drawEllipse(QPointF(19 * S, 10 * S), 2.5 * S, 2.5 * S)

    p.end()
    return px


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    px = draw_icon()
    px.save(str(OUT))
    print(f"Ícone gerado: {OUT}")


if __name__ == "__main__":
    main()
