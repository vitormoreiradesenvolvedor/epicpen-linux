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
        QPainterPath, QRadialGradient, QLinearGradient, QFont,
    )
    from PyQt6.QtCore import Qt, QPointF, QRectF
except ImportError:
    print("PyQt6 não encontrado — ícone não gerado.", file=sys.stderr)
    sys.exit(0)

SIZE = 256
OUT  = Path(__file__).parent.parent / "resources" / "icons" / "epicpen.png"


def draw_icon() -> QPixmap:
    px = QPixmap(SIZE, SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # ── Fundo: círculo com gradiente radial ──────────────────────────────
    bg = QRadialGradient(QPointF(SIZE * 0.45, SIZE * 0.38), SIZE * 0.6)
    bg.setColorAt(0.0, QColor(55, 55, 80))
    bg.setColorAt(1.0, QColor(18, 18, 30))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawEllipse(6, 6, SIZE - 12, SIZE - 12)

    # Borda externa com gradiente
    border_grad = QLinearGradient(0, 0, SIZE, SIZE)
    border_grad.setColorAt(0.0, QColor(120, 120, 180, 180))
    border_grad.setColorAt(1.0, QColor(60, 60, 100, 80))
    p.setPen(QPen(QBrush(border_grad), 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(6, 6, SIZE - 12, SIZE - 12)

    # ── Caneta (corpo) ───────────────────────────────────────────────────
    # Diagonal de cima-direita para baixo-esquerda
    # Pontos do corpo retangular rotacionado ~45°
    def pt(x, y): return QPointF(x, y)

    body = QPainterPath()
    body.moveTo(pt(68,  190))   # ponta inferior-esquerda
    body.lineTo(pt(82,  204))   # ponta inferior-direita
    body.lineTo(pt(200, 80))    # topo superior-direita
    body.lineTo(pt(188, 62))    # topo superior-esquerda
    body.closeSubpath()

    body_grad = QLinearGradient(68, 190, 200, 62)
    body_grad.setColorAt(0.0, QColor(200, 200, 220))
    body_grad.setColorAt(0.5, QColor(240, 240, 255))
    body_grad.setColorAt(1.0, QColor(170, 170, 200))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(body_grad))
    p.drawPath(body)

    # Faixa decorativa central na caneta
    stripe = QPainterPath()
    stripe.moveTo(pt(120, 148))
    stripe.lineTo(pt(134, 134))
    stripe.lineTo(pt(148, 148))
    stripe.lineTo(pt(134, 162))
    stripe.closeSubpath()
    p.setBrush(QBrush(QColor(100, 140, 220, 200)))
    p.drawPath(stripe)

    # Borracha / topo da caneta
    eraser = QPainterPath()
    eraser.moveTo(pt(188, 62))
    eraser.lineTo(pt(200, 80))
    eraser.lineTo(pt(214, 66))
    eraser.lineTo(pt(202, 48))
    eraser.closeSubpath()
    p.setBrush(QBrush(QColor(255, 160, 160)))
    p.drawPath(eraser)

    # ── Ponta da caneta (vermelho vivo) ──────────────────────────────────
    tip = QPainterPath()
    tip.moveTo(pt(68, 190))
    tip.lineTo(pt(82, 204))
    tip.lineTo(pt(56, 218))
    tip.closeSubpath()
    p.setBrush(QBrush(QColor(220, 50, 50)))
    p.drawPath(tip)

    # Ponto brilhante na ponta
    p.setBrush(QBrush(QColor(255, 255, 255, 200)))
    p.drawEllipse(QPointF(65, 208), 4, 4)

    # ── Traço vermelho no fundo (efeito de anotação) ─────────────────────
    stroke_pen = QPen(QColor(220, 60, 60, 160), 9,
                      Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin)
    p.setPen(stroke_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(pt(48,  145))
    path.cubicTo(pt(90, 165), pt(130, 125), pt(190, 148))
    p.drawPath(path)

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
