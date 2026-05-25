#!/usr/bin/env python3
"""
Gera resources/icons/epicpen.png (256x256) usando PyQt6.
Usa QImage em vez de QPixmap — funciona sem display (headless, Wayland, CI).
"""
import sys
from pathlib import Path

try:
    from PyQt6.QtGui import (
        QGuiApplication, QImage, QPainter, QColor, QBrush, QPen,
        QPainterPath, QLinearGradient,
    )
    from PyQt6.QtCore import Qt, QPointF, QRectF
except ImportError:
    print("PyQt6 não encontrado — ícone não gerado.", file=sys.stderr)
    sys.exit(0)

SIZE = 256
OUT  = Path(__file__).parent.parent / "resources" / "icons" / "epicpen.png"
S    = SIZE / 28  # escala relativa ao viewBox SVG original (28×28)


def draw_icon() -> QImage:
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Fundo: rounded rect com gradiente (#4f8eff → #7c5fff)
    grad = QLinearGradient(QPointF(0, 0), QPointF(SIZE, SIZE))
    grad.setColorAt(0.0, QColor(0x4f, 0x8e, 0xff))
    grad.setColorAt(1.0, QColor(0x7c, 0x5f, 0xff))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(0, 0, SIZE, SIZE), 7 * S, 7 * S)

    # Forma "A" / chevron: M8 20 L14 8 L20 20
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

    # Ponto branco: cx=19 cy=10 r=2.5 opacity=.9
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, int(0.9 * 255))))
    p.drawEllipse(QPointF(19 * S, 10 * S), 2.5 * S, 2.5 * S)

    p.end()
    return img


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = draw_icon()
    if not img.save(str(OUT)):
        print(f"ERRO: falha ao guardar {OUT}", file=sys.stderr)
        sys.exit(1)
    print(f"Ícone gerado: {OUT}")


if __name__ == "__main__":
    main()
